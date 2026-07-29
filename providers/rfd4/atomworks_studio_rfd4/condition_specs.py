"""UI-agnostic registry for conditions exposed by interactive editors.

Both the PyMOL plugin and Annotation Studio consume this registry. Adding a
standard mask, enum, group, or vector condition here makes its schema and
summary behavior available to both front ends; only genuinely custom editors
need a front-end-specific override.

Vendored into atomworks-studio from RFD4-Proteina ``rfproteina.condition_specs``
(RFD4 commit 0e9050ca, before its upstream removal; the submodule now tracks main).
Mirrors symbols from ``rfproteina.conditions``; re-verify parity when bumping the RFD4 submodule.
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Literal

from atomworks.ml.conditions import C_CRD, C_DIS, C_IDX, C_SEQ
from atomworks.ml.conditions.base import ConditionBase
from atomworks.ml.transforms.dssp import SSEnum
from rfproteina.conditions import (
    C_CENTROID,
    C_CRDGRP,
    C_HB,
    C_HOT,
    C_IDXGRP,
    C_INTCRD,
    C_NCYS,
    C_RAS,
    C_SEC,
    HBondEnum,
    RasaEnum,
)

ConditionKind = Literal["mask", "enum", "integer", "vector3", "pair", "operation"]
ConditionLevel = Literal["atom", "residue", "chain", "token"]


@dataclass(frozen=True)
class InteractiveConditionSpec:
    key: str
    condition: type[ConditionBase]
    label: str
    kind: ConditionKind
    level: ConditionLevel
    default: Any
    enum_cls: type[IntEnum] | None = None
    # Enum values to hide from the editor (e.g. DSSP sentinels the model does not accept).
    enum_exclude: frozenset[int] = frozenset()
    invert_mask: bool = False
    count_unit: Literal["atoms", "residues", "chains", "groups", "pairs"] = "atoms"
    plugin_editor: Literal["native", "index", "mask", "enum", "group", "centroid", "pair"] = "mask"
    plugin_hide_display: bool = False

    def schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "kind": self.kind,
            "level": self.level,
            "default": self.default,
            "label": self.label,
        }
        if self.enum_cls is not None:
            schema["values"] = {
                member.name: member.value
                for member in self.enum_cls
                if member.value not in self.enum_exclude
            }
        return schema


INTERACTIVE_CONDITION_SPECS = (
    InteractiveConditionSpec(
        "sequence",
        C_SEQ,
        "Sequence conditioning",
        "mask",
        "residue",
        False,
        count_unit="residues",
        plugin_editor="native",
    ),
    InteractiveConditionSpec(
        "coordinate",
        C_CRD,
        "Coordinate conditioning",
        "mask",
        "atom",
        False,
        plugin_editor="native",
    ),
    InteractiveConditionSpec(
        "index",
        C_IDX,
        "Index conditioning",
        "mask",
        "residue",
        True,
        invert_mask=True,
        plugin_editor="index",
    ),
    InteractiveConditionSpec("hotspot", C_HOT, "Hotspot", "mask", "atom", False),
    InteractiveConditionSpec(
        "not_cysteine",
        C_NCYS,
        "Not cysteine",
        "mask",
        "residue",
        False,
        count_unit="residues",
    ),
    InteractiveConditionSpec(
        "rasa",
        C_RAS,
        "RASA",
        "enum",
        "atom",
        RasaEnum.UNSPECIFIED.value,
        enum_cls=RasaEnum,
        plugin_editor="enum",
    ),
    InteractiveConditionSpec(
        "hbond",
        C_HB,
        "Hydrogen bonds",
        "enum",
        "atom",
        HBondEnum.NONE.value,
        enum_cls=HBondEnum,
        plugin_editor="enum",
    ),
    InteractiveConditionSpec(
        "secondary_structure",
        C_SEC,
        "Secondary structure",
        "enum",
        "residue",
        SSEnum.NONE.value,
        enum_cls=SSEnum,
        enum_exclude=frozenset({SSEnum.NON_PROTEIN.value}),
        count_unit="residues",
        plugin_editor="enum",
    ),
    InteractiveConditionSpec(
        "index_group",
        C_IDXGRP,
        "Index groups",
        "integer",
        "token",
        -1,
        count_unit="groups",
        plugin_editor="group",
        plugin_hide_display=True,
    ),
    InteractiveConditionSpec(
        "coordinate_group",
        C_CRDGRP,
        "Coordinate groups",
        "integer",
        "atom",
        C_CRDGRP.UNKNOWN_COORDS,
        count_unit="groups",
        plugin_editor="group",
        plugin_hide_display=True,
    ),
    InteractiveConditionSpec(
        "chain_centroid",
        C_CENTROID,
        "Chain centroid",
        "vector3",
        "chain",
        None,
        count_unit="chains",
        plugin_editor="centroid",
    ),
    InteractiveConditionSpec(
        "internal_coordinate",
        C_INTCRD,
        "Internal coordinate",
        "operation",
        "atom",
        None,
        plugin_editor="native",
    ),
    InteractiveConditionSpec(
        "distance",
        C_DIS,
        "Distance restraints",
        "pair",
        "atom",
        None,
        count_unit="pairs",
        plugin_editor="pair",
    ),
)

CONDITION_SPECS_BY_KEY = {spec.key: spec for spec in INTERACTIVE_CONDITION_SPECS}
