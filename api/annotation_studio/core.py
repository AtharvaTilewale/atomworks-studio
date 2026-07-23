# ruff: noqa: I001
"""Headless AtomWorks document model used by the web and VS Code editors."""

from __future__ import annotations

import copy
from io import StringIO
from itertools import pairwise
from typing import Any, Literal

import biotite.structure as struc
import numpy as np
from atomworks.constants import MASKED
from atomworks.io import parse
from atomworks.io.config import ParseConfig
from atomworks.io.transforms.atom_array import add_id_and_entity_annotations, add_iid_annotations
from atomworks.io.utils.atom_array_plus import AtomArrayPlus, as_atom_array_plus, concatenate_atom_array_plus
from atomworks.io.utils.chain import create_chain_id_generator
from atomworks.io.utils.io_utils import CIFWriteConfig, to_cif_string
from atomworks.io.utils.standard_annotations import S_SEGMAX, S_SEGMIN
from atomworks.ml.utils.expandable_segment import (
    collapse_expandable_segments,
    insert_expandable_segment,
    realize_expandable_segments,
)

from .contracts import ConditionProvider, ValidationProvider

ConditionScope = Literal["atom", "residue", "chain"]

RESIDUE_CODES = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
}

BOND_TYPES = {
    "SINGLE": struc.BondType.SINGLE,
    "DOUBLE": struc.BondType.DOUBLE,
    "TRIPLE": struc.BondType.TRIPLE,
    "AROMATIC": struc.BondType.AROMATIC,
    "AROMATIC_SINGLE": struc.BondType.AROMATIC_SINGLE,
    "AROMATIC_DOUBLE": struc.BondType.AROMATIC_DOUBLE,
    "COORDINATION": struc.BondType.COORDINATION,
    "ANY": struc.BondType.ANY,
}


def _refresh_chain_derived_annotations(array: AtomArrayPlus) -> None:
    add_id_and_entity_annotations(array, overwrite=True)
    add_iid_annotations(array, overwrite=True)
    if "label_entity_id" in array.get_annotation_categories():
        array.set_annotation("label_entity_id", (array.chain_entity + 1).astype(np.int32))


def _first_array(result: dict[str, Any]) -> AtomArrayPlus:
    assemblies = result.get("assemblies", {})
    array = next(iter(assemblies.values())) if assemblies else result["asym_unit"]
    if isinstance(array, struc.AtomArrayStack):
        array = array[0]
    array = as_atom_array_plus(array)
    _refresh_chain_derived_annotations(array)
    return array


def parse_annotated_cif(cif_text: str) -> AtomArrayPlus:
    """Parse with the same default preset as the PyMOL plugin's Annotated CIF option."""
    config = ParseConfig.from_preset(
        "minimal",
        file_type="cif",
        build_assembly=["1"],
        load_standard_annotations=True,
    )
    return _first_array(parse(StringIO(cif_text), config=config))


class StudioDocument:
    """Authoritative mutable AtomWorks structure with bounded undo history."""

    def __init__(
        self,
        array: AtomArrayPlus,
        name: str = "structure",
        *,
        condition_provider: ConditionProvider | None = None,
        validation_provider: ValidationProvider | None = None,
    ):
        if condition_provider is None or validation_provider is None:
            from .rfd4_provider import CONDITION_PROVIDER, VALIDATION_PROVIDER

            condition_provider = condition_provider or CONDITION_PROVIDER
            validation_provider = validation_provider or VALIDATION_PROVIDER
        self.array = array
        self.name = name
        self.condition_provider = condition_provider
        self.validation_provider = validation_provider
        self._undo: list[AtomArrayPlus] = []

    @classmethod
    def from_cif(
        cls,
        cif_text: str,
        name: str = "structure",
        *,
        condition_provider: ConditionProvider | None = None,
        validation_provider: ValidationProvider | None = None,
    ) -> StudioDocument:
        return cls(
            parse_annotated_cif(cif_text),
            name,
            condition_provider=condition_provider,
            validation_provider=validation_provider,
        )

    def clone(self) -> StudioDocument:
        return StudioDocument(
            copy.deepcopy(self.array),
            self.name,
            condition_provider=self.condition_provider,
            validation_provider=self.validation_provider,
        )

    def snapshot(self) -> None:
        self._undo.append(copy.deepcopy(self.array))
        if len(self._undo) > 10:
            self._undo.pop(0)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self.array = self._undo.pop()
        return True

    def annotation_details(self, condition_name: str | None = None) -> list[dict[str, Any]]:
        """Return explicitly stored, non-default design annotations and atom membership."""

        entries: list[dict[str, Any]] = []

        def atom_indices(mask: np.ndarray) -> list[int]:
            return np.flatnonzero(mask).astype(int).tolist()

        for spec in self.condition_provider.definitions:
            condition = spec.condition
            if spec.kind == "mask":
                if not condition.has_mask(self.array):
                    continue
                mask = condition.mask(self.array, default="generate")
                if spec.invert_mask:
                    mask = ~mask
                if not mask.any():
                    continue
                indices = atom_indices(mask)
                count = len(indices)
                if spec.count_unit == "residues":
                    count = int(mask[struc.get_residue_starts(self.array)].sum())
                entries.append(
                    {
                        "name": spec.key,
                        "label": spec.label,
                        "count": count,
                        "unit": spec.count_unit,
                        "values": [
                            {
                                "label": "Unindexed" if spec.invert_mask else "Active",
                                "count": len(indices),
                                "atom_indices": indices,
                            }
                        ],
                    }
                )
                continue

            if not condition.has_annotation(self.array):
                continue
            annotation = condition.annotation(self.array, default="raise")
            mask = condition.mask(self.array, default="generate")
            values: list[dict[str, Any]] = []

            if spec.kind == "enum":
                for member in spec.enum_values:
                    if member.value == spec.default:
                        continue
                    member_mask = mask & (annotation == member.value)
                    if member_mask.any():
                        indices = atom_indices(member_mask)
                        values.append(
                            {
                                "label": member.name,
                                "value": int(member.value),
                                "count": len(indices),
                                "atom_indices": indices,
                            }
                        )
            elif spec.kind == "integer":
                for group in np.unique(annotation):
                    if group == spec.default:
                        continue
                    group_mask = annotation == group
                    indices = atom_indices(group_mask)
                    values.append(
                        {
                            "label": f"Group {int(group)}",
                            "value": int(group),
                            "count": len(indices),
                            "atom_indices": indices,
                        }
                    )
            elif spec.kind == "vector3":
                for chain in np.unique(self.array.chain_iid[mask]):
                    chain_mask = mask & (self.array.chain_iid == chain)
                    indices = atom_indices(chain_mask)
                    xyz = annotation[indices[0]]
                    values.append(
                        {
                            "label": f"Chain {chain}",
                            "value": [float(value) for value in xyz],
                            "count": len(indices),
                            "atom_indices": indices,
                        }
                    )

            if values:
                entries.append(
                    {
                        "name": spec.key,
                        "label": spec.label,
                        "count": len(values) if spec.count_unit in {"groups", "chains"} else sum(
                            value["count"] for value in values
                        ),
                        "unit": spec.count_unit,
                        "values": values,
                    }
                )

        if condition_name is not None:
            if condition_name not in self.condition_provider.definitions_by_key:
                raise KeyError(f"unsupported condition: {condition_name}")
            return [entry for entry in entries if entry["name"] == condition_name]
        return entries

    def stored_annotation_fields(self, field_name: str | None = None) -> list[dict[str, Any]]:
        """Inventory every serialized condition/mask/annotation field, including unknown future ones."""

        fields: list[dict[str, Any]] = []

        def json_value(value: Any) -> Any:
            value = value.item() if isinstance(value, np.generic) else value
            if isinstance(value, float) and not np.isfinite(value):
                return None
            return value

        categories = sorted(
            category
            for category in self.array.get_annotation_categories()
            if category.startswith(("mask_", "condition_", "annotation_"))
        )
        for category in categories:
            annotation = np.asarray(self.array.get_annotation(category))
            values: list[dict[str, Any]] = []
            truncated = False
            if annotation.ndim == 1:
                unique, counts = np.unique(annotation, return_counts=True)
                truncated = len(unique) > 64
                for value, count in zip(unique[:64], counts[:64], strict=True):
                    if isinstance(value, float | np.floating) and np.isnan(value):
                        value_mask = np.isnan(annotation)
                    else:
                        value_mask = annotation == value
                    indices = np.flatnonzero(value_mask).astype(int).tolist()
                    values.append(
                        {
                            "label": str(json_value(value)),
                            "value": json_value(value),
                            "count": int(count),
                            "atom_indices": indices,
                        }
                    )
            else:
                finite = np.isfinite(annotation).any(axis=tuple(range(1, annotation.ndim)))
                indices = np.flatnonzero(finite).astype(int).tolist()
                values.append(
                    {
                        "label": f"Stored {tuple(annotation.shape[1:])} values",
                        "count": len(indices),
                        "atom_indices": indices,
                    }
                )
            fields.append(
                {
                    "name": category,
                    "label": category,
                    "count": len(annotation),
                    "unit": "rows",
                    "values": values,
                    "truncated": truncated,
                }
            )

        if field_name is not None:
            return [field for field in fields if field["name"] == field_name]
        return fields

    def scalar_annotation_fields(self, field_name: str | None = None) -> list[dict[str, Any]]:
        """Expose per-atom confidence fields as stable color buckets."""

        palette = [
            ("Very low (<50)", 0.0, 50.0, {"name": "red", "value": 0xEF5350}),
            ("Low (50-70)", 50.0, 70.0, {"name": "orange", "value": 0xFFA726}),
            ("Confident (70-90)", 70.0, 90.0, {"name": "yellow", "value": 0xFFD54F}),
            ("Very high (≥90)", 90.0, float("inf"), {"name": "blue", "value": 0x42A5F5}),
        ]
        fields = []
        for category in sorted(self.array.get_annotation_categories()):
            lowered = category.lower()
            if category != "b_factor" and "plddt" not in lowered and "confidence" not in lowered:
                continue
            annotation = np.asarray(self.array.get_annotation(category))
            if annotation.ndim != 1 or not np.issubdtype(annotation.dtype, np.number):
                continue
            values = annotation.astype(float)
            finite = np.isfinite(values)
            if not finite.any() or np.nanmax(np.abs(values[finite])) == 0:
                continue
            display_values = values * 100.0 if np.nanmax(values[finite]) <= 1.0 else values
            groups = []
            for label, minimum, maximum, color in palette:
                mask = finite & (display_values >= minimum) & (display_values < maximum)
                indices = np.flatnonzero(mask).astype(int).tolist()
                if indices:
                    groups.append(
                        {
                            "label": label,
                            "count": len(indices),
                            "atom_indices": indices,
                            "color": color,
                        }
                    )
            if groups:
                fields.append(
                    {
                        "name": category,
                        "label": "pLDDT / confidence" if category == "b_factor" else category,
                        "count": int(finite.sum()),
                        "unit": "atoms",
                        "values": groups,
                    }
                )
        if field_name is not None:
            return [field for field in fields if field["name"] == field_name]
        return fields

    def atom_annotation_details(self, atom_index: int) -> dict[str, Any]:
        index = int(self.resolve_indices([atom_index])[0])
        annotations = []
        for entry in self.annotation_details():
            matching_values = []
            for value in entry["values"]:
                if index in value["atom_indices"]:
                    matching_values.append({key: item for key, item in value.items() if key != "atom_indices"})
            if matching_values:
                annotations.append(
                    {
                        "name": entry["name"],
                        "label": entry["label"],
                        "values": matching_values,
                    }
                )
        stored_fields = []
        for field in self.stored_annotation_fields():
            for value in field["values"]:
                if index in value["atom_indices"]:
                    stored_fields.append(
                        {
                            "name": field["name"],
                            "value": value.get("value"),
                            "label": value["label"],
                        }
                    )
                    break
        scalar_fields = []
        for field in self.scalar_annotation_fields():
            value = self.array.get_annotation(field["name"])[index]
            scalar_fields.append(
                {
                    "name": field["name"],
                    "label": field["label"],
                    "value": float(value),
                }
            )
        chain_key = "chain_iid" if "chain_iid" in self.array.get_annotation_categories() else "chain_id"
        result: dict[str, Any] = {
            "atom_index": index,
            "chain": str(self.array.get_annotation(chain_key)[index]),
            "residue_id": int(self.array.res_id[index]),
            "residue_name": str(self.array.res_name[index]),
            "atom_name": str(self.array.atom_name[index]),
            "annotations": annotations,
            "stored_fields": stored_fields,
            "scalar_fields": scalar_fields,
        }
        if "charge" in self.array.get_annotation_categories():
            result["formal_charge"] = int(self.array.charge[index])
        return result

    def resolve_indices(self, indices: list[int], scope: ConditionScope = "atom") -> np.ndarray:
        if not indices:
            raise ValueError("at least one atom must be selected")
        selected = np.unique(np.asarray(indices, dtype=np.int64))
        if selected[0] < 0 or selected[-1] >= len(self.array):
            raise IndexError(f"atom index outside [0, {len(self.array) - 1}]")
        if scope == "atom":
            return selected

        mask = np.zeros(len(self.array), dtype=bool)
        if scope == "chain":
            chain_key = "chain_iid" if "chain_iid" in self.array.get_annotation_categories() else "chain_id"
            chains = np.unique(self.array.get_annotation(chain_key)[selected])
            mask = np.isin(self.array.get_annotation(chain_key), chains)
        elif scope == "residue":
            chain_key = "chain_iid" if "chain_iid" in self.array.get_annotation_categories() else "chain_id"
            ins_code = (
                self.array.ins_code
                if "ins_code" in self.array.get_annotation_categories()
                else np.full(len(self.array), "")
            )
            for index in selected:
                mask |= (
                    (self.array.get_annotation(chain_key) == self.array.get_annotation(chain_key)[index])
                    & (self.array.res_id == self.array.res_id[index])
                    & (ins_code == ins_code[index])
                )
        else:
            raise ValueError(f"unsupported selection scope: {scope}")
        return np.flatnonzero(mask)

    def set_condition(
        self,
        name: str,
        indices: list[int],
        *,
        enabled: bool = True,
        value: int | list[float] | None = None,
        scope: ConditionScope = "atom",
    ) -> None:
        if name not in self.condition_provider.definitions_by_key:
            raise ValueError(f"unsupported condition: {name}")
        selected = self.resolve_indices(indices, scope)
        spec = self.condition_provider.definitions_by_key[name]
        condition = spec.condition
        self.snapshot()
        try:
            if spec.kind == "mask":
                mask = condition.mask(self.array, default="generate").copy()
                mask[selected] = not enabled if spec.invert_mask else enabled
                condition.set_mask(self.array, mask)
                self.condition_provider.after_condition_update(name, self.array)
                return

            if spec.kind == "vector3":
                annotation = condition.annotation(self.array, default="generate").copy()
                mask = condition.mask(self.array, default="generate").copy()
                if enabled:
                    vector = np.asarray(value, dtype=np.float32)
                    if vector.shape != (3,) or not np.isfinite(vector).all():
                        raise ValueError("chain_centroid requires three finite coordinates")
                    annotation[selected] = vector
                    mask[selected] = True
                else:
                    annotation[selected] = np.nan
                    mask[selected] = False
                condition.set_annotation(self.array, annotation)
                condition.set_mask(self.array, mask)
                return

            annotation = condition.annotation(self.array, default="generate").copy()
            default = spec.default
            if enabled and value is None:
                raise ValueError(f"{name} requires a value")
            if enabled and spec.kind == "enum":
                allowed = {member.value for member in spec.enum_values}
                if value not in allowed:
                    raise ValueError(f"{name} value must be one of {sorted(allowed)}")
            if enabled and spec.kind == "integer" and not isinstance(value, int):
                raise ValueError(f"{name} requires an integer value")
            annotation[selected] = value if enabled else default
            condition.set_annotation(self.array, annotation)
            if spec.kind == "enum":
                mask = condition.mask(self.array, default="generate").copy()
                mask[selected] = enabled and value != default
                condition.set_mask(self.array, mask)
        except Exception:
            self.array = self._undo.pop()
            raise

    def set_charge(self, indices: list[int], value: int | None = None, delta: int | None = None) -> None:
        selected = self.resolve_indices(indices)
        self.snapshot()
        if "charge" not in self.array.get_annotation_categories():
            self.array.set_annotation("charge", np.zeros(len(self.array), dtype=int))
        if value is not None:
            self.array.charge[selected] = int(value)
        elif delta is not None:
            self.array.charge[selected] += int(delta)
        else:
            self.array = self._undo.pop()
            raise ValueError("value or delta is required")

    def edit_bond(self, atom1: int, atom2: int, action: Literal["add", "remove"], bond_type: str = "SINGLE") -> None:
        selected = self.resolve_indices([atom1, atom2])
        if len(selected) != 2:
            raise ValueError("a bond requires two different atoms")
        self.snapshot()
        try:
            if self.array.bonds is None:
                self.array.bonds = struc.BondList(len(self.array))
            if action == "add":
                self.array.bonds.add_bond(int(selected[0]), int(selected[1]), BOND_TYPES[bond_type])
            elif action == "remove":
                self.array.bonds.remove_bond(int(selected[0]), int(selected[1]))
            else:
                raise ValueError(f"unsupported bond action: {action}")
        except Exception:
            self.array = self._undo.pop()
            raise

    def delete_atoms(self, indices: list[int], close_residue_gaps: bool = False) -> None:
        selected = self.resolve_indices(indices)
        keep = np.ones(len(self.array), dtype=bool)
        keep[selected] = False
        if not keep.any():
            raise ValueError("cannot delete every atom")
        self.snapshot()
        if not close_residue_gaps:
            self.array = self.array[keep]
            return
        residue_bounds = struc.get_residue_starts(self.array, add_exclusive_stop=True)
        last_kept_start: dict[str, int] = {}
        for start, stop in pairwise(residue_bounds):
            if keep[start:stop].any():
                last_kept_start[str(self.array.chain_iid[start])] = int(start)

        adjusted = self.array.res_id.copy()
        shift_by_chain: dict[str, int] = {}
        for start, stop in pairwise(residue_bounds):
            chain = str(self.array.chain_iid[start])
            shift = shift_by_chain.get(chain, 0)
            if not keep[start:stop].any():
                if start < last_kept_start.get(chain, -1):
                    shift_by_chain[chain] = shift + 1
                continue
            adjusted[start:stop] = self.array.res_id[start:stop] - shift
        self.array = self.array[keep]
        self.array.res_id = adjusted[keep]

    def reset_residue_ids(self) -> None:
        self.snapshot()
        for chain in np.unique(self.array.chain_iid):
            mask = self.array.chain_iid == chain
            self.array.res_id[mask] -= int(self.array.res_id[mask].min()) - 1

    def insert_segment(
        self,
        minimum: int,
        maximum: int,
        atom_index: int | None = None,
        *,
        before: bool = True,
        new_chain: bool = False,
        chain_id: str | None = None,
        chain_type: str | None = None,
    ) -> None:
        if atom_index is not None and not new_chain:
            selected = int(self.resolve_indices([atom_index])[0])
            bounds = struc.get_residue_starts(self.array, add_exclusive_stop=True)
            residue = int(np.searchsorted(bounds[:-1], selected, side="right")) - 1
            atom_index = int(bounds[residue] if before else bounds[residue + 1] - 1)
        self.snapshot()
        try:
            self.array = insert_expandable_segment(
                self.array,
                minimum,
                maximum,
                atom_idx=atom_index,
                insert_before=before,
                new_chain=new_chain,
                new_chain_id=chain_id,
                new_chain_type=chain_type,
            )
        except Exception:
            self.array = self._undo.pop()
            raise

    def realize_segments(self, seed: int | None = None) -> None:
        self.snapshot()
        self.array = realize_expandable_segments(self.array, np.random.default_rng(seed))

    def collapse_segments(self) -> None:
        self.snapshot()
        self.array = collapse_expandable_segments(self.array)

    def align_to(self, reference: StudioDocument) -> dict[str, float | int]:
        """Rigidly align common protein-backbone atoms to a reference document."""

        def identity_map(array: AtomArrayPlus) -> dict[tuple[str, int, str, str], int]:
            ins_codes = (
                array.ins_code
                if "ins_code" in array.get_annotation_categories()
                else np.full(len(array), "")
            )
            mapping: dict[tuple[str, int, str, str], int] = {}
            duplicates: set[tuple[str, int, str, str]] = set()
            for index in range(len(array)):
                key = (
                    str(array.chain_id[index]),
                    int(array.res_id[index]),
                    str(ins_codes[index]),
                    str(array.atom_name[index]),
                )
                if key in mapping:
                    duplicates.add(key)
                else:
                    mapping[key] = index
            for key in duplicates:
                mapping.pop(key, None)
            return mapping

        moving = identity_map(self.array)
        target = identity_map(reference.array)
        common = sorted(set(moving) & set(target))
        backbone = {"N", "CA", "C", "O"}
        common_backbone = [key for key in common if key[3] in backbone]
        keys = common_backbone if len(common_backbone) >= 3 else common
        pairs = [
            (moving[key], target[key])
            for key in keys
            if np.isfinite(self.array.coord[moving[key]]).all()
            and np.isfinite(reference.array.coord[target[key]]).all()
        ]
        if len(pairs) < 3:
            raise ValueError("alignment requires at least three common finite-coordinate atoms")

        moving_indices, target_indices = zip(*pairs, strict=True)
        moving_coords = self.array.coord[list(moving_indices)].astype(np.float64)
        target_coords = reference.array.coord[list(target_indices)].astype(np.float64)
        moving_center = moving_coords.mean(axis=0)
        target_center = target_coords.mean(axis=0)
        centered_moving = moving_coords - moving_center
        centered_target = target_coords - target_center
        u_matrix, _, v_transpose = np.linalg.svd(centered_moving.T @ centered_target)
        if np.linalg.det(u_matrix @ v_transpose) < 0:
            v_transpose[-1] *= -1
        rotation = u_matrix @ v_transpose
        before = float(np.sqrt(np.mean(np.sum((moving_coords - target_coords) ** 2, axis=1))))
        aligned_matches = centered_moving @ rotation + target_center
        after = float(np.sqrt(np.mean(np.sum((aligned_matches - target_coords) ** 2, axis=1))))

        self.snapshot()
        finite = np.isfinite(self.array.coord).all(axis=1)
        transformed = self.array.coord[finite].astype(np.float64)
        self.array.coord[finite] = ((transformed - moving_center) @ rotation + target_center).astype(
            self.array.coord.dtype
        )
        return {
            "matched_atom_count": len(pairs),
            "rmsd_before": before,
            "rmsd_after": after,
        }

    def combine(self, others: list[StudioDocument]) -> None:
        arrays = [copy.deepcopy(self.array), *(copy.deepcopy(other.array) for other in others)]
        used: set[str] = set()
        for array in arrays:
            chains = np.unique(array.chain_id)
            available = {chain for chain in chains if chain not in used}
            generator = create_chain_id_generator(used | available)
            renames = {chain: next(generator) for chain in chains if chain in used}
            if renames:
                array.chain_id = np.array([renames.get(chain, chain) for chain in array.chain_id])
            used.update(available)
            used.update(renames.values())
        self.snapshot()
        self.array = concatenate_atom_array_plus(arrays)
        _refresh_chain_derived_annotations(self.array)

    def validate(self, full_pipeline: bool = False) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        ins_code = (
            self.array.ins_code
            if "ins_code" in self.array.get_annotation_categories()
            else np.full(len(self.array), "")
        )
        keys = [
            (
                str(self.array.chain_iid[index]),
                int(self.array.res_id[index]),
                str(ins_code[index]),
                str(self.array.atom_name[index]),
            )
            for index in range(len(self.array))
        ]
        if len(set(keys)) != len(keys):
            issues.append(
                {"severity": "error", "check": "atom_identity", "message": "atom identity keys are not unique"}
            )
        if np.isinf(self.array.coord).any():
            issues.append({"severity": "error", "check": "coordinates", "message": "coordinates contain infinity"})
        issues.extend(self.validation_provider.validate(self.array, full_pipeline=full_pipeline))
        return issues

    def sequence_view(self) -> list[dict[str, Any]]:
        """Return a compact chain/residue model for the interactive sequence strip."""
        chain_key = "chain_iid" if "chain_iid" in self.array.get_annotation_categories() else "chain_id"
        residue_bounds = struc.get_residue_starts(self.array, add_exclusive_stop=True)
        if len(residue_bounds) < 2:
            return []

        condition_masks: dict[str, np.ndarray] = {}
        if self.condition_provider is not None:
            for key in ("sequence", "coordinate", "index", "hotspot"):
                definition = self.condition_provider.definitions_by_key.get(key)
                if definition is not None:
                    condition_masks[key] = definition.condition.mask(self.array, default="generate")

        has_segments = S_SEGMIN.has_mask(self.array) and S_SEGMAX.has_mask(self.array)
        segment_mask = S_SEGMIN.mask(self.array) if has_segments else np.zeros(len(self.array), dtype=bool)
        segment_min = S_SEGMIN.annotation(self.array) if has_segments else np.zeros(len(self.array), dtype=int)
        segment_max = S_SEGMAX.annotation(self.array) if has_segments else np.zeros(len(self.array), dtype=int)
        chains: list[dict[str, Any]] = []

        residue_index = 0
        while residue_index < len(residue_bounds) - 1:
            start = int(residue_bounds[residue_index])
            chain_id = str(self.array.get_annotation(chain_key)[start])
            items: list[dict[str, Any]] = []
            while residue_index < len(residue_bounds) - 1:
                start = int(residue_bounds[residue_index])
                stop = int(residue_bounds[residue_index + 1])
                if str(self.array.get_annotation(chain_key)[start]) != chain_id:
                    break
                if segment_mask[start]:
                    realized = str(self.array.res_name[start]) == MASKED
                    minimum = int(segment_min[start])
                    maximum = int(segment_max[start])
                    run_start = residue_index
                    while residue_index + 1 < len(residue_bounds) - 1:
                        next_start = int(residue_bounds[residue_index + 1])
                        if (
                            str(self.array.get_annotation(chain_key)[next_start]) != chain_id
                            or not segment_mask[next_start]
                            or (str(self.array.res_name[next_start]) == MASKED) != realized
                            or int(segment_min[next_start]) != minimum
                            or int(segment_max[next_start]) != maximum
                        ):
                            break
                        residue_index += 1
                    run_stop = int(residue_bounds[residue_index + 1])
                    items.append(
                        {
                            "kind": "segment",
                            "atom_indices": list(range(int(residue_bounds[run_start]), run_stop)),
                            "minimum": minimum,
                            "maximum": maximum,
                            "realized": realized,
                            "realized_length": residue_index - run_start + 1 if realized else None,
                        }
                    )
                else:
                    conditions = [
                        key for key, mask in condition_masks.items() if bool(np.any(mask[start:stop]))
                    ]
                    residue_name = str(self.array.res_name[start])
                    items.append(
                        {
                            "kind": "residue",
                            "atom_indices": list(range(start, stop)),
                            "residue_id": int(self.array.res_id[start]),
                            "residue_name": residue_name,
                            "code": RESIDUE_CODES.get(residue_name, residue_name),
                            "conditions": conditions,
                        }
                    )
                residue_index += 1
            chains.append({"chain_id": chain_id, "items": items})
        return chains

    def summary(self) -> dict[str, Any]:
        chain_key = "chain_iid" if "chain_iid" in self.array.get_annotation_categories() else "chain_id"
        details = self.annotation_details()
        annotations = [
            {
                **{key: value for key, value in entry.items() if key != "values"},
                "values": [
                    {key: value for key, value in item.items() if key != "atom_indices"} for item in entry["values"]
                ],
            }
            for entry in details
        ]
        stored_fields = [
            {
                **{key: value for key, value in field.items() if key != "values"},
                "values": [
                    {key: value for key, value in item.items() if key != "atom_indices"} for item in field["values"]
                ],
            }
            for field in self.stored_annotation_fields()
        ]
        scalar_fields = [
            {
                **{key: value for key, value in field.items() if key != "values"},
                "values": [
                    {key: value for key, value in item.items() if key != "atom_indices"} for item in field["values"]
                ],
            }
            for field in self.scalar_annotation_fields()
        ]
        return {
            "name": self.name,
            "atom_count": len(self.array),
            "chain_ids": sorted(str(value) for value in np.unique(self.array.get_annotation(chain_key))),
            "sequence": self.sequence_view(),
            "condition_counts": {entry["name"]: entry["count"] for entry in details},
            "annotations": annotations,
            "stored_fields": stored_fields,
            "scalar_fields": scalar_fields,
            "undo_depth": len(self._undo),
            "validation": self.validate(),
        }

    def to_cif(self) -> str:
        config = CIFWriteConfig(
            include_entity_categories=True,
            save_standard_annotations=True,
            chain_disambiguation="chain_iid",
        )
        return to_cif_string(self.array, config=config)
