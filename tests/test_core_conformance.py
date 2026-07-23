"""Semantic conformance tests against native AtomWorks condition APIs."""

from pathlib import Path

import numpy as np
import pytest
from annotation_studio.core import StudioDocument
from annotation_studio.rfd4_provider import CONDITION_PROVIDER

from rfproteina.condition_specs import INTERACTIVE_CONDITION_SPECS

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "data" / "benchmarks" / "SAM_hbonds.cif"
CONDITIONS = {
    definition.key: definition.condition for definition in CONDITION_PROVIDER.definitions
}


@pytest.fixture
def document() -> StudioDocument:
    return StudioDocument.from_cif(FIXTURE.read_text(), FIXTURE.stem)


@pytest.mark.parametrize(
    ("name", "value", "scope"),
    [
        ("sequence", None, "residue"),
        ("coordinate", None, "atom"),
        ("index", None, "residue"),
        ("hotspot", None, "atom"),
        ("rasa", 3, "atom"),
        ("hbond", 1, "atom"),
        ("index_group", 7, "residue"),
        ("coordinate_group", 2, "atom"),
        ("chain_centroid", [1.25, -2.5, 3.75], "chain"),
    ],
)
def test_every_plugin_condition_survives_cif_roundtrip(
    document: StudioDocument,
    name: str,
    value: int | list[float] | None,
    scope: str,
) -> None:
    document.set_condition(name, [0], enabled=True, value=value, scope=scope)
    reparsed = StudioDocument.from_cif(document.to_cif())
    condition = CONDITIONS[name]

    expected_mask = condition.mask(document.array, default="generate")
    actual_mask = condition.mask(reparsed.array, default="generate")
    assert np.array_equal(actual_mask, expected_mask)

    if name not in {"sequence", "coordinate", "index", "hotspot"}:
        expected = condition.annotation(document.array, default="generate")
        actual = condition.annotation(reparsed.array, default="generate")
        assert np.allclose(actual, expected, equal_nan=True)


def test_export_is_semantically_idempotent(document: StudioDocument) -> None:
    first = StudioDocument.from_cif(document.to_cif())
    second = StudioDocument.from_cif(first.to_cif())

    assert len(first.array) == len(second.array)
    assert np.allclose(first.array.coord, second.array.coord, equal_nan=True)
    assert first.summary()["condition_counts"] == second.summary()["condition_counts"]


def test_align_to_restores_common_backbone_coordinates(document: StudioDocument) -> None:
    reference = StudioDocument.from_cif(document.to_cif())
    rotation = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    finite = np.isfinite(document.array.coord).all(axis=1)
    document.array.coord[finite] = document.array.coord[finite] @ rotation + [11.0, -7.0, 3.0]

    result = document.align_to(reference)

    assert result["matched_atom_count"] >= 3
    assert result["rmsd_before"] > 1.0
    assert result["rmsd_after"] < 1e-5
    assert np.allclose(document.array.coord[finite], reference.array.coord[finite], atol=1e-4)


def test_add_modify_delete_annotations_survive_each_export(document: StudioDocument) -> None:
    all_indices = list(range(len(document.array)))
    document.set_condition("hotspot", all_indices, enabled=False)
    document.set_condition("hotspot", [0, 1], enabled=True)
    added = StudioDocument.from_cif(document.to_cif())
    assert added.annotation_details("hotspot")[0]["values"][0]["atom_indices"] == [0, 1]

    added.set_condition("hotspot", [0], enabled=False)
    added.set_condition("rasa", [1], enabled=True, value=3)
    modified = StudioDocument.from_cif(added.to_cif())
    assert modified.annotation_details("hotspot")[0]["values"][0]["atom_indices"] == [1]
    rasa = modified.annotation_details("rasa")[0]
    assert rasa["values"][0]["label"] == "EXPOSED"
    assert rasa["values"][0]["value"] == 3
    assert rasa["values"][0]["atom_indices"] == [1]

    modified.set_condition("hotspot", [1], enabled=False)
    modified.set_condition("rasa", [1], enabled=False)
    cleared = StudioDocument.from_cif(modified.to_cif())
    assert cleared.annotation_details("hotspot") == []
    assert cleared.annotation_details("rasa") == []


def test_atom_annotation_details_match_condition_membership(document: StudioDocument) -> None:
    document.set_condition("hotspot", [0], enabled=True)
    details = document.atom_annotation_details(0)
    assert details["atom_index"] == 0
    assert any(annotation["name"] == "hotspot" for annotation in details["annotations"])
    assert not any(annotation["name"] == "hotspot" for annotation in document.atom_annotation_details(1)["annotations"])


def test_shared_registry_drives_studio_and_all_stored_fields_are_visible(document: StudioDocument) -> None:
    assert list(CONDITIONS) == [spec.key for spec in INTERACTIVE_CONDITION_SPECS]
    fields = {field["name"] for field in document.stored_annotation_fields()}
    expected = {
        category
        for category in document.array.get_annotation_categories()
        if category.startswith(("mask_", "condition_", "annotation_"))
    }
    assert fields == expected
    assert {"mask_pistack_1_atom", "condition_saltbridge_1_atom"} <= fields
    atom = document.atom_annotation_details(0)
    assert atom["stored_fields"]


def test_plddt_confidence_is_exposed_in_colored_buckets(document: StudioDocument) -> None:
    confidence = np.full(len(document.array), 95.0)
    confidence[:3] = [25.0, 60.0, 80.0]
    document.array.set_annotation("b_factor", confidence)

    field = document.scalar_annotation_fields("b_factor")[0]
    assert [value["label"] for value in field["values"]] == [
        "Very low (<50)",
        "Low (50-70)",
        "Confident (70-90)",
        "Very high (≥90)",
    ]
    assert field["values"][0]["color"]["name"] == "red"
    assert field["values"][-1]["color"]["name"] == "blue"
    assert document.atom_annotation_details(2)["scalar_fields"][0]["value"] == 80.0


def test_charge_bond_delete_and_undo(document: StudioDocument) -> None:
    original_count = len(document.array)
    document.set_charge([0], value=1)
    document.edit_bond(0, 1, "add", "SINGLE")
    document.delete_atoms([0])
    assert len(document.array) == original_count - 1
    assert document.undo()
    assert len(document.array) == original_count
    assert int(document.array.charge[0]) == 1


def test_expandable_segment_insert_realize_collapse(document: StudioDocument) -> None:
    document.insert_segment(2, 4, atom_index=0, before=False)
    collapsed_count = len(document.array)
    document.realize_segments(seed=7)
    assert len(document.array) >= collapsed_count + 1
    document.collapse_segments()
    assert len(document.array) == collapsed_count


def test_expandable_only_protein_chain_survives_cif_roundtrip(document: StudioDocument) -> None:
    document.insert_segment(
        50,
        120,
        new_chain=True,
        chain_id="Z",
        chain_type="polypeptide(L)",
    )

    reparsed = StudioDocument.from_cif(document.to_cif())
    chain = reparsed.array[reparsed.array.chain_id == "Z"]

    assert len(chain) == 1
    assert chain.chain_type[0] == document.array[document.array.chain_id == "Z"].chain_type[0]
    assert bool(chain.is_polymer[0])


def test_combine_resolves_chain_conflicts_and_reset_ids(document: StudioDocument) -> None:
    other = StudioDocument.from_cif(FIXTURE.read_text(), "other")
    original_count = len(document.array)
    document.combine([other])

    assert len(document.array) == 2 * original_count
    assert len(np.unique(document.array.chain_id)) == 4
    document.reset_residue_ids()
    for chain in np.unique(document.array.chain_iid):
        assert int(document.array.res_id[document.array.chain_iid == chain].min()) == 1
