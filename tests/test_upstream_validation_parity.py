"""Differential save validation against the real, isolated RFD4 runtime.

Set ANNOTATION_STUDIO_RFD4_VALIDATION_PYTHON to the model environment's Python to
run these checks. The reference process imports upstream reporting directly;
Studio's process goes through its public validation provider and private worker.
"""

import json
import os
import pickle
import shlex
import subprocess
from pathlib import Path

import numpy as np
import pytest
from atomworks.ml.conditions import C_CRD, C_IDX
from atomworks_studio_editing.document import StudioDocument
from atomworks_studio_rfd4 import CONDITION_PROVIDER, VALIDATION_PROVIDER
from atomworks_studio_rfd4.model import C_CRDGRP, C_HB, C_IDXGRP, C_INTCRD, HBondEnum, conditions, source_root

_RUNTIME = os.environ.get("ANNOTATION_STUDIO_RFD4_VALIDATION_PYTHON")
pytestmark = pytest.mark.skipif(not _RUNTIME, reason="requires explicitly configured real RFD4 validation Python")

_REFERENCE = r"""
import contextlib, json, pickle, sys
sys.path.insert(0, sys.argv[1])
with contextlib.redirect_stdout(sys.stderr):
    from rfproteina.conditions_reporting import (
        run_pipeline_validation,
        check_index_group_res_id_gaps,
        check_index_group_molecule_consistency,
    )
    arrays = pickle.load(sys.stdin.buffer)
    reports = {}
    for name, array in arrays.items():
        reports[name] = []
        for hook in (run_pipeline_validation, check_index_group_res_id_gaps, check_index_group_molecule_consistency):
            try:
                reports[name].extend(hook(array.copy()))
            except Exception as error:
                reports[name].append(f"[hook_error] {error}")
print(json.dumps(reports))
"""


@pytest.fixture(scope="module")
def differential_inputs():
    base = StudioDocument.from_cif(
        (Path(__file__).parent / "fixtures/ppi_pdl1.cif").read_text(),
        condition_provider=CONDITION_PROVIDER,
        validation_provider=VALIDATION_PROVIDER,
    )
    arrays = {"valid": base.array.copy()}
    authored = base.clone()
    authored.set_condition("sequence", [0])
    authored.set_condition("coordinate", [0])
    authored.set_condition("cath_label", [0], value="2.60.40")
    arrays["authored"] = authored.array.copy()
    expandable = base.clone()
    expandable.insert_segment(2, 7, new_chain=True, chain_id="X", chain_type="polypeptide(L)")
    arrays["expandable"] = expandable.array.copy()
    invalid = base.array.copy()
    invalid.coord[0] += 1000
    arrays["invalid_bond_geometry"] = invalid
    invalid = base.array.copy()
    C_HB.set_annotation(invalid, np.full(len(invalid), HBondEnum.BOTH, dtype=int))
    arrays["invalid_hbond"] = invalid
    invalid = base.array.copy()
    probability = conditions.SEQUENCE_PROBABILITY_CONDITIONS[0]
    probability.set_annotation(invalid, np.full(len(invalid), 1.1, dtype=np.float32))
    arrays["invalid_probability"] = invalid
    invalid = base.array.copy()
    group = C_IDXGRP.default_annotation(invalid)
    first_chain = invalid.chain_id == invalid.chain_id[0]
    invalid.res_id[first_chain & (invalid.res_id > invalid.res_id[0])] += 2
    C_IDXGRP.set_annotation(invalid, group)
    arrays["invalid_index_gap"] = invalid
    invalid = base.array.copy()
    C_IDX.set_annotation(invalid, invalid.res_id.copy())
    groups = C_IDXGRP.default_annotation(invalid)
    groups[invalid.res_id == invalid.res_id[0]] = int(groups.max()) + 10
    C_IDXGRP.set_annotation(invalid, groups)
    arrays["invalid_molecule_groups"] = invalid
    invalid = base.array.copy()
    C_CRD.set_annotation(invalid, np.full((len(invalid), 3), np.nan))
    C_CRDGRP.set_annotation(invalid, np.ones(len(invalid), dtype=int))
    targets = C_INTCRD.default_annotation(invalid)
    targets[0] = [1, 2, 3]
    invalid.set_annotation(C_INTCRD.full_name, targets)
    arrays["invalid_partial_targets"] = invalid
    result = subprocess.run(
        [*shlex.split(_RUNTIME), "-c", _REFERENCE, str(source_root().parent)],
        input=pickle.dumps(arrays, protocol=5),
        capture_output=True,
        check=True,
        timeout=240,
    )
    return arrays, json.loads(result.stdout)


@pytest.mark.parametrize(
    "name",
    [
        "valid",
        "authored",
        "expandable",
        "invalid_bond_geometry",
        "invalid_hbond",
        "invalid_probability",
        "invalid_index_gap",
        "invalid_molecule_groups",
        "invalid_partial_targets",
    ],
)
def test_upstream_presave_reports_match_exactly(differential_inputs, name):
    arrays, reference = differential_inputs
    array = arrays[name]
    before = pickle.dumps(array, protocol=5)
    actual = VALIDATION_PROVIDER.validate(array, full_pipeline=True)
    messages = [f"[{issue['check']}] {issue['message']}" for issue in actual]
    assert messages == reference[name]
    assert pickle.dumps(array, protocol=5) == before
    if name in {"valid", "authored", "expandable"}:
        assert messages == []
    else:
        assert messages, f"Invalid differential fixture {name} must actually exercise rejection"
    if name == "expandable":
        assert VALIDATION_PROVIDER.validate(array, full_pipeline=True) == actual
        assert pickle.dumps(array, protocol=5) == before
