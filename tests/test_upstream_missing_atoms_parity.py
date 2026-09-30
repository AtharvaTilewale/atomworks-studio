"""Compare Studio's generic save check with the actual upstream PyMOL panel."""

import logging
import pickle
from pathlib import Path

import numpy as np
import pytest
from atomworks.io.utils.atom_array_plus import as_atom_array_plus
from atomworks.io.utils.ccd import atom_array_from_ccd_code
from atomworks_studio_editing import validation
from atomworks_studio_editing.document import StudioDocument


@pytest.fixture(scope="module")
def upstream_panel():
    pytest.importorskip("pymol", reason="upstream PyMOL panel requires PyMOL")
    pytest.importorskip("pymol.Qt", reason="upstream PyMOL panel requires Qt")
    return pytest.importorskip(
        "atomworks.pymol_plugin.panel",
        reason="historical oracle requires AtomWorks before plugin extraction",
        exc_type=ImportError,
    )


@pytest.fixture(scope="module")
def missing_atom_inputs():
    array = StudioDocument.from_cif((Path(__file__).parent / "fixtures/ppi_pdl1.cif").read_text()).array
    complete = as_atom_array_plus(atom_array_from_ccd_code("ALA"))
    complete = complete[complete.element != "H"]
    cases = {"complete": complete}
    # Removing one heavy atom must actually exercise the warning, rather than
    # merely comparing two empty lists from complete fixtures.
    remove_index = int(np.flatnonzero(complete.atom_name == "CB")[0])
    cases["incomplete"] = complete[np.arange(len(complete)) != remove_index]
    hydrogen = complete[:1].copy()
    hydrogen.element[:] = "H"
    hydrogen.atom_name[:] = "H"
    hydrogen.coord[0] += [0.8, 0.0, 0.0]
    with_hydrogen = complete[:1] + hydrogen + complete[1:]
    cases["retained_hydrogen"] = with_hydrogen
    cases["incomplete_with_hydrogen"] = with_hydrogen[np.arange(len(with_hydrogen)) != remove_index + 1]
    expandable = StudioDocument(array.copy(), "expandable")
    expandable.insert_segment(2, 5, new_chain=True, chain_id="X", chain_type="polypeptide(L)")
    cases["collapsed_segment"] = expandable.array
    return cases


@pytest.mark.parametrize(
    "name",
    [
        "complete",
        "incomplete",
        "retained_hydrogen",
        "incomplete_with_hydrogen",
        "collapsed_segment",
    ],
)
def test_missing_atom_report_matches_upstream(upstream_panel, missing_atom_inputs, name, caplog):
    array = missing_atom_inputs[name]
    original = pickle.dumps(array, protocol=5)
    expected = upstream_panel._check_atom_array_invariants(array)
    upstream_warnings = [
        record.getMessage() for record in caplog.records if "Missing-atom check failed" in record.getMessage()
    ]
    caplog.clear()
    actual = validation.validate_atom_array_for_save(array)
    studio_warnings = [
        record.getMessage() for record in caplog.records if "Missing-atom check failed" in record.getMessage()
    ]
    assert studio_warnings == upstream_warnings
    messages = [f"[{issue['check']}] {issue['message']}" for issue in actual]
    assert messages == expected
    assert pickle.dumps(array, protocol=5) == original
    if name == "incomplete":
        # Current AtomWorks can fail template concatenation and log-and-skip.
        # Preserve that behavior explicitly instead of treating an empty report
        # as proof that a visibly incomplete residue was validated successfully.
        assert (messages and messages[0].startswith("[missing_atoms] ")) or upstream_warnings
    if name == "complete":
        assert messages == []


def test_preparation_failure_matches_upstream_logging_and_result(
    upstream_panel, missing_atom_inputs, monkeypatch, caplog
):
    array = missing_atom_inputs["complete"]
    original = pickle.dumps(array, protocol=5)

    def unavailable(candidate, *, config):
        candidate.coord[:] = 0  # Both callers must protect their input on failure.
        raise RuntimeError("CCD unavailable for parity fixture")

    monkeypatch.setattr(upstream_panel, "prepare_atom_array", unavailable)
    monkeypatch.setattr(validation, "prepare_atom_array", unavailable)
    with caplog.at_level(logging.WARNING):
        assert upstream_panel._check_atom_array_invariants(array) == []
        upstream_warnings = [record.getMessage() for record in caplog.records]
        caplog.clear()
        assert validation.validate_atom_array_for_save(array) == []
        studio_warnings = [record.getMessage() for record in caplog.records]
    assert (
        studio_warnings
        == upstream_warnings
        == [
            "Missing-atom check failed: CCD unavailable for parity fixture",
        ]
    )
    assert pickle.dumps(array, protocol=5) == original


def test_successful_completion_uses_identical_config_and_warning(upstream_panel, missing_atom_inputs, monkeypatch):
    array = missing_atom_inputs["incomplete"]
    received_configs = []

    def completed(candidate, *, config):
        received_configs.append(config)
        assert candidate is not array
        return candidate + candidate[:1]

    # Exercise successful chemistry completion even while the pinned upstream's
    # template concatenation rejects some incomplete AtomArrayPlus inputs.
    monkeypatch.setattr(upstream_panel, "prepare_atom_array", completed)
    monkeypatch.setattr(validation, "prepare_atom_array", completed)
    expected = upstream_panel._check_atom_array_invariants(array)
    actual = validation.validate_atom_array_for_save(array)
    assert (
        [f"[{issue['check']}] {issue['message']}" for issue in actual]
        == expected
        == [
            "[missing_atoms] Structure is missing 1 heavy atoms that would be "
            "added by prepare_atom_array. Check for incomplete residues."
        ]
    )
    assert received_configs[0] == received_configs[1]
