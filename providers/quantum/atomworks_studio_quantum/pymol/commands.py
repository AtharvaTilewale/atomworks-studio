"""The ``aq_load_result`` PyMOL command and the electronic-state summary of the objects it loads.

Only imported inside a running PyMOL process, by :func:`atomworks_studio_quantum.pymol.register`.
"""

from __future__ import annotations

import weakref

import numpy as np
from atomworks.io.config import ParseConfig
from atomworks.io.utils.atom_array_plus import AtomArrayPlus
from atomworks_studio_pymol.bridge import load_structure
from atomworks_studio_pymol.state import STATE
from pymol import cmd

from atomworks_studio_quantum.pymol.results import electronic_state_text, find_result, summary

# Based on aq's QM_PARSE_CONFIG (atomworks_quantum.io), because the file is authoritative: no atoms added or
# removed, and waters, ligands, hydrogens (link hydrogens of cluster caps too) and bonds to metals kept. It also
# keeps arginine atom names, ligands at symmetry centres, long bonds and struct_conn bonds exactly as aq wrote
# them. aq's extra atom_site columns (fixed, is_cap, ...) are not loaded: the plugin's save does not write them.
_PARSE_CONFIG = ParseConfig(
    add_missing_atoms=False,
    load_standard_annotations=True,
    remove_waters=False,
    remove_ccds=(),
    add_bond_types_from_struct_conn=("covale", "disulf", "metalc"),
    build_assembly=None,
    hydrogen_policy="keep",
    fix_arginines=False,
    fix_ligands_at_symmetry_centers=False,
    long_bond_policy="keep",
    struct_conn_distance_policy="keep",
)

# Total charge and multiplicity of each object aq_load_result loaded, with the tracked array they describe and
# its formal charges then. Structural edits and undo replace that array (PluginState.set_array), and the charge
# editor changes its formal charges in place; after either, the state no longer applies.
_STATES: dict[str, tuple[weakref.ref[AtomArrayPlus], np.ndarray, int, int]] = {}


def aq_load_result(directory: str, name: str = "") -> None:
    """Load an atomworks-quantum result directory.

    USAGE: ``aq_load_result directory [, name]``

    The final structure becomes a tracked, editable AtomWorks object named ``name`` (default: the directory
    name). Each trajectory ``<field>.cif`` (optimization, MD, NEB images, IRC branches) becomes a multi-state
    object ``<name>_<field>``, disabled until you enable it. The result summary from ``result.json`` is printed.
    """
    try:
        result = find_result(directory)
    except (OSError, ValueError) as error:
        print(f"AtomWorks-Quantum: {error}")
        return
    obj_name = cmd.get_legal_name(name or result.path.name)
    trajectories = {cmd.get_legal_name(f"{obj_name}_{field}"): path for field, path in result.trajectories.items()}
    # Objects and selections share PyMOL's names, which ignore case by default (setting ignore_case).
    wanted = {candidate.casefold() for candidate in (obj_name, *trajectories)}
    if taken := [existing for existing in cmd.get_names("all") if existing.casefold() in wanted]:
        print(
            f"AtomWorks-Quantum: {', '.join(taken)} already exist; delete them or choose another name "
            f"(aq_load_result {result.path}, <name>)."
        )
        return

    load_structure(result.structure, config=_PARSE_CONFIG, obj_name=obj_name)
    if (state := result.electronic_state) is not None:
        array = STATE.arrays[obj_name]
        _STATES[obj_name] = (weakref.ref(array), _formal_charges(array), *state)
    for trajectory, path in trajectories.items():
        cmd.load(str(path), trajectory)
        cmd.disable(trajectory)

    loaded = [f"Loaded '{obj_name}' (editable)"]
    loaded += [f"'{trajectory}' ({cmd.count_states(trajectory)} states, disabled)" for trajectory in trajectories]
    print(f"AtomWorks-Quantum: {', '.join(loaded)}.\n{summary(result)}")


def summarize_electronic_state(array: AtomArrayPlus) -> str:
    """Summary section: the active object's total charge and multiplicity, if aq_load_result loaded it.

    The panel passes a copy of the active object's array, so the state is looked up by the active object; it is
    reported only while that object's tracked array is still the one aq_load_result loaded, with the same formal
    charges.
    """
    name = STATE.active
    entry = _STATES.get(name) if name else None
    if entry is None:
        return ""
    loaded, formal_charges, charge, multiplicity = entry
    if loaded() is not STATE.arrays.get(name) or not np.array_equal(_formal_charges(array), formal_charges):
        return ""
    return electronic_state_text(charge, multiplicity)


def _formal_charges(array: AtomArrayPlus) -> np.ndarray:
    """A copy of the formal charges; zeros if the array has none, as the charge editor reads them."""
    if "charge" in array.get_annotation_categories():
        return array.charge.copy()
    return np.zeros(len(array), dtype=int)
