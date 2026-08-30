"""Shared, UI-neutral validation for mutable AtomWorks structures.

Moved into atomworks-studio (from atomworks ``io.utils.validation``, commit 8e4b2c39) as a
shared editor primitive; see :mod:`atomworks_studio_editing.structure_mutations`.
"""

import numpy as np
from atomworks.io.utils.atom_array_plus import AtomArrayPlus


def validate_atom_array(array: AtomArrayPlus) -> list[dict[str, str]]:
    """Return structural issues required by all interactive editors."""
    issues: list[dict[str, str]] = []
    ins_code = array.ins_code if "ins_code" in array.get_annotation_categories() else np.full(len(array), "")
    chain_key = "chain_iid" if "chain_iid" in array.get_annotation_categories() else "chain_id"
    keys = [
        (
            str(array.get_annotation(chain_key)[index]),
            int(array.res_id[index]),
            str(ins_code[index]),
            str(array.atom_name[index]),
        )
        for index in range(len(array))
    ]
    if len(set(keys)) != len(keys):
        issues.append({"severity": "error", "check": "atom_identity", "message": "atom identity keys are not unique"})
    if np.isinf(array.coord).any():
        issues.append({"severity": "error", "check": "coordinates", "message": "coordinates contain infinity"})
    return issues
