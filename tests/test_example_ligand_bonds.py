"""The example rebuild states the aromatic ligand bond orders it would otherwise copy as AROM."""

from pathlib import Path
from runpy import run_path

import numpy as np
import pytest
from atomworks_studio_editing.document import StudioDocument
from biotite.structure.bonds import BondType

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "packages/api/annotation_studio/examples"
state_aromatic_bond_orders = run_path(str(ROOT / "scripts/build_hit_examples.py"))["state_aromatic_bond_orders"]

# enzyme_design and inverse_folding both carry the nitrophenyl phosphate ligand L:0,
# whose benzene ring the design source stores with unstated (AROMATIC) bond orders.
LIGAND_EXAMPLES = ["enzyme_design.cif", "inverse_folding.cif"]
RING = [("C2", "C3"), ("C2", "C4"), ("C3", "C5"), ("C4", "C6"), ("C5", "C7"), ("C6", "C7")]


def _load(name):
    return StudioDocument.from_cif((EXAMPLES / name).read_text(), name, parse_options={"preset": "minimal"})


@pytest.mark.parametrize("name", LIGAND_EXAMPLES)
def test_shipped_ligand_has_no_unstated_aromatic_bonds(name):
    bonds = _load(name).array.bonds.as_array()
    assert not any(order == BondType.AROMATIC for *_ignored, order in bonds), (
        f"{name}: ships BondType.AROMATIC bonds, which protonation rejects"
    )


@pytest.mark.parametrize("name", LIGAND_EXAMPLES)
def test_rebuild_restates_source_aromatic_ring_as_shipped(name):
    """Reverting the ring to the source's AROM bonds and re-running the builder helper
    reproduces the shipped bond orders, so a documented rebuild no longer regenerates
    unstated bonds."""
    doc = _load(name)
    array = doc.array
    shipped = array.bonds.as_array().copy()
    ligand = array.res_name == "L:0"
    atom = lambda nm: int(np.flatnonzero(ligand & (array.atom_name == nm))[0])  # noqa: E731
    for first, second in RING:
        array.bonds.add_bond(atom(first), atom(second), BondType.AROMATIC)
    assert state_aromatic_bond_orders(array) == len(RING)
    np.testing.assert_array_equal(array.bonds.as_array(), shipped)
    # And the restated ligand can be protonated, which the AROM source could not.
    assert doc.protonate(7.4)["hydrogen_count"] > 0


def test_provenance_ligand_note_is_the_one_the_rebuild_writes():
    """Re-running the rebuild must regenerate, not delete, the checked-in ligand provenance note."""
    import json

    ligand_bond_order_note = run_path(str(ROOT / "scripts/build_hit_examples.py"))["ligand_bond_order_note"]
    provenance = json.loads((EXAMPLES / "hit-example-provenance.json").read_text())
    for name, record in provenance.items():
        if name in LIGAND_EXAMPLES:
            assert record["ligand_bond_orders"] == ligand_bond_order_note(len(RING))
        else:
            assert "ligand_bond_orders" not in record, f"{name}: the rebuild states no bonds there"
