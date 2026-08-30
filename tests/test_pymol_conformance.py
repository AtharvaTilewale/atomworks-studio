# ruff: noqa: I001
"""Cross-check browser mutations against the existing PyMOL bridge."""

from pathlib import Path

import numpy as np
import pytest

pymol = pytest.importorskip("pymol", reason="PyMOL is not installed")
# This cross-check drives the real RFD4 PyMOL editors, which import the full RFD4 runtime
# (the rfproteina package init + datapipes pull environs, torch, and rootutils). Skip the
# whole module unless that runtime is installed -- e.g. in a CPU-only Studio environment.
pytest.importorskip("environs", reason="full RFD4 runtime not installed")
pytest.importorskip("rootutils", reason="full RFD4 runtime not installed")
pymol.finish_launching(["pymol", "-qck"])

from pymol import cmd  # noqa: E402

from atomworks.io.config import ParseConfig  # noqa: E402
from atomworks_studio_pymol.bridge import load_structure, selection_to_indices  # noqa: E402
from atomworks_studio_pymol.editors import editor_for  # noqa: E402
from atomworks_studio_pymol.editors.widgets import PairValueEditor  # noqa: E402
from atomworks_studio_pymol.state import STATE  # noqa: E402
from atomworks.ml.conditions import C_DIS  # noqa: E402
from rfproteina.conditions import C_HOT, C_RAS  # noqa: E402
from atomworks_studio_rfd4.condition_contract import CONDITION_DEFINITIONS  # noqa: E402
from atomworks_studio_rfd4.pymol.editors import _run_pipeline_validation  # noqa: E402

from annotation_studio.core import StudioDocument  # noqa: E402
from annotation_studio.rfd4_provider import CONDITION_PROVIDER, VALIDATION_PROVIDER  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "SAM_hbonds.cif"


def _rfd4_document(cif: str, name: str) -> StudioDocument:
    return StudioDocument.from_cif(
        cif,
        name,
        condition_provider=CONDITION_PROVIDER,
        validation_provider=VALIDATION_PROVIDER,
    )


def test_selection_and_condition_outputs_match_pymol_plugin() -> None:
    cmd.reinitialize()
    STATE.clear_all()
    config = ParseConfig.from_preset(
        "minimal",
        build_assembly=["1"],
        load_standard_annotations=True,
    )
    name = load_structure(FIXTURE, config=config, obj_name="conformance")
    plugin_array = STATE.arrays[name]
    document = _rfd4_document(FIXTURE.read_text(), FIXTURE.stem)

    chain = str(plugin_array.chain_iid[0])
    residue = int(plugin_array.res_id[0])
    expression = f"{name} and chain {chain} and resi {residue}"
    indices = sorted(selection_to_indices(expression, STATE.index_maps[name]))
    assert indices
    assert indices == document.resolve_indices([indices[0]], "residue").tolist()

    hotspot = C_HOT.mask(plugin_array, default="generate").copy()
    hotspot[indices] = True
    C_HOT.set_mask(plugin_array, hotspot)
    rasa = C_RAS.annotation(plugin_array, default="generate").copy()
    rasa[indices] = 3
    C_RAS.set_annotation(plugin_array, rasa)
    rasa_mask = C_RAS.mask(plugin_array, default="generate").copy()
    rasa_mask[indices] = True
    C_RAS.set_mask(plugin_array, rasa_mask)

    document.set_condition("hotspot", indices, enabled=True)
    document.set_condition("rasa", indices, enabled=True, value=3)

    assert np.array_equal(
        C_HOT.mask(document.array, default="generate"),
        C_HOT.mask(plugin_array, default="generate"),
    )
    assert np.array_equal(
        C_RAS.annotation(document.array, default="generate"),
        C_RAS.annotation(plugin_array, default="generate"),
    )

    studio_summary = {entry["name"]: entry for entry in document.annotation_details()}
    plugin_hotspot_count = int(C_HOT.mask(plugin_array, default="generate").sum())
    assert studio_summary["hotspot"]["count"] == plugin_hotspot_count
    plugin_rasa = C_RAS.annotation(plugin_array, default="generate")
    plugin_rasa_mask = C_RAS.mask(plugin_array, default="generate")
    plugin_exposed_count = int((plugin_rasa_mask & (plugin_rasa == 3)).sum())
    exposed = next(value for value in studio_summary["rasa"]["values"] if value["label"] == "EXPOSED")
    assert exposed["count"] == plugin_exposed_count


def test_every_shared_condition_roundtrips_into_pymol(tmp_path: Path) -> None:
    document = _rfd4_document(FIXTURE.read_text(), "all_conditions")
    mutations = [
        ("sequence", True, None, "residue"),
        ("coordinate", True, None, "atom"),
        ("index", True, None, "residue"),
        ("hotspot", True, None, "atom"),
        ("rasa", True, 3, "atom"),
        ("hbond", True, 1, "atom"),
        ("index_group", True, 7, "residue"),
        ("coordinate_group", True, 2, "atom"),
        ("chain_centroid", True, [1.0, 2.0, 3.0], "chain"),
    ]
    for condition, enabled, value, scope in mutations:
        document.set_condition(condition, [0], enabled=enabled, value=value, scope=scope)
    document.edit_distance_restraint(0, 1, "set", 3.25)

    exported = tmp_path / "all_conditions.cif"
    exported.write_text(document.to_cif())
    cmd.reinitialize()
    STATE.clear_all()
    name = load_structure(
        exported,
        config=ParseConfig.from_preset("minimal", build_assembly=["1"], load_standard_annotations=True),
        obj_name="all_conditions",
    )
    plugin_array = STATE.arrays[name]
    assert len(plugin_array) == len(document.array)

    for definition in CONDITION_DEFINITIONS:
        condition = definition.condition
        if definition.kind == "pair":
            plugin_annotation = condition.annotation(plugin_array, default="raise")
            document_annotation = condition.annotation(document.array, default="raise")
            assert plugin_annotation.pairs.tolist() == document_annotation.pairs.tolist()
            np.testing.assert_allclose(plugin_annotation.values, document_annotation.values)
            continue
        assert np.array_equal(
            condition.mask(plugin_array, default="generate"),
            condition.mask(document.array, default="generate"),
        ), definition.key
        if condition.full_name in document.array.get_annotation_categories():
            np.testing.assert_allclose(
                condition.annotation(plugin_array, default="raise"),
                condition.annotation(document.array, default="raise"),
                equal_nan=True,
                err_msg=definition.key,
            )


def test_distance_condition_uses_existing_pair_editor() -> None:
    from atomworks_studio_rfd4.pymol import condition_registry

    condition_registry.apply()

    assert editor_for(C_DIS) is PairValueEditor


def test_pymol_and_studio_share_structural_validation() -> None:
    pytest.importorskip("rootutils", reason="full RFD4 validation dependencies are tested in GPU acceptance")
    document = _rfd4_document(FIXTURE.read_text(), "validation")
    document.array.coord[0, 0] = np.inf

    expected = next(issue for issue in document.validate() if issue["check"] == "coordinates")
    assert f"[coordinates] {expected['message']}" in _run_pipeline_validation(document.array)
