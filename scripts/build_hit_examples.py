"""Rebuild the hit-based examples without repairing or sampling their source structures.

Run with Studio's Python environment. Source paths are explicit so no private data
or machine-specific checkout paths are needed by the shipped examples.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from atomworks.enums import ChainType
from atomworks.io.utils.standard_annotations import STANDARD_ANNOTATIONS
from atomworks.ml.conditions import C_CRD, C_IDX, C_SEQ
from atomworks_studio_editing.document import StudioDocument
from atomworks_studio_editing.providers import load_providers
from atomworks_studio_editing.structure_mutations import refresh_chain_derived_annotations
from atomworks_studio_rfd4.internal_coordinate import set_internal_coordinate_conditioning
from atomworks_studio_rfd4.model import C_CRDGRP, C_IDXGRP
from atomworks_studio_rfd4.reporting import sync_index_groups
from biotite.structure import get_residue_starts

PROVIDERS = load_providers()  # Registers model annotations before parsing.
BACKBONE = ["N", "CA", "C", "O"]


def document(path):
    return StudioDocument.from_cif(
        path.read_text(),
        condition_provider=PROVIDERS.condition_provider,
        validation_provider=PROVIDERS.validation_provider,
    )


def keys(a):
    return list(zip(a.chain_id, a.res_id, a.ins_code, a.res_name, a.atom_name, strict=True))


def compare_arrays(expected, actual):
    """Check all atom identities, geometry, chemistry and saved annotations after reload."""
    lookup = {key: i for i, key in enumerate(keys(actual))}
    assert len(lookup) == len(actual) == len(expected)
    actual = actual[[lookup[key] for key in keys(expected)]]
    np.testing.assert_allclose(actual.coord, expected.coord, atol=0.001, equal_nan=True)
    np.testing.assert_array_equal(actual.charge, expected.charge)
    np.testing.assert_array_equal(actual.element, expected.element)
    np.testing.assert_array_equal(actual.bonds.as_array(), expected.bonds.as_array())
    for field in STANDARD_ANNOTATIONS:
        if not field.has_annotation(expected):
            continue
        x, y = field.annotation(expected), field.annotation(actual)
        if field.n_body == 2:
            np.testing.assert_array_equal(x.pairs, y.pairs)
            np.testing.assert_allclose(x.values, y.values, atol=0.001, equal_nan=True)
        elif np.asarray(x).dtype.kind in "fc":
            np.testing.assert_allclose(x, y, atol=0.001, equal_nan=True, err_msg=field.full_name)
        else:
            np.testing.assert_array_equal(x, y, err_msg=field.full_name)


def retain(doc, mask):
    doc.array = doc.array[mask]
    refresh_chain_derived_annotations(doc.array)


def pte_inverse_folding(source):
    doc = document(source)
    a = doc.array
    # Preserve the existing motif/ligand sequence masks and tip targets exactly.
    values = C_CRD.annotation(a, default="generate").copy()
    backbone = (a.chain_id == "A") & np.isin(a.atom_name, BACKBONE)
    old_fixed = C_CRD.mask(a, default="generate")
    values[backbone & ~old_fixed] = a.coord[backbone & ~old_fixed]
    C_CRD.set_annotation(a, values)
    C_CRDGRP.set_annotation(a, C_CRDGRP.default_annotation(a))
    return doc


def pte_scaffold(source):
    doc = document(source)
    a = doc.array
    fixed = C_CRD.mask(a, default="generate")
    keep = a.chain_id != "A"
    for start, stop in zip(get_residue_starts(a), get_residue_starts(a, add_exclusive_stop=True)[1:], strict=True):
        if fixed[start:stop].any():
            keep[start:stop] = True
    retain(doc, keep)
    a = doc.array
    motif = a.chain_id == "A"
    kcx = motif & (a.res_name == "KCX")
    assert len(get_residue_starts(a[motif])) == 8 and kcx.any()
    C_IDX.set_annotation(a, ~(motif & ~kcx))
    # Every motif residue is a separate island; ligand indexing is retained.
    sync_index_groups(a, preserve_manual_edits=False)
    anchor = int(np.flatnonzero(kcx)[0])
    doc.insert_segment(75, 100, atom_index=anchor, before=True)
    anchor = int(np.flatnonzero((doc.array.chain_id == "A") & (doc.array.res_name == "KCX"))[-1])
    doc.insert_segment(75, 100, atom_index=anchor, before=False)
    a = doc.array
    refresh_chain_derived_annotations(a)
    sync_index_groups(a, preserve_manual_edits=False)
    groups = C_IDXGRP.annotation(a, default="generate").copy()
    next_group = int(groups.max()) + 1
    for start, stop in zip(get_residue_starts(a), get_residue_starts(a, add_exclusive_stop=True)[1:], strict=True):
        if not C_IDX.mask(a, default="generate")[start]:
            groups[start:stop] = next_group
            next_group += 1
    C_IDXGRP.set_annotation(a, groups)
    # Insertion connects its neighboring residues. The unindexed islands are
    # independently placed, so keep only the segment-KCX-segment connections.
    indexed = C_IDX.mask(a, default="generate")
    for left, right, _ in a.bonds.as_array():
        if indexed[left] != indexed[right]:
            a.bonds.remove_bond(int(left), int(right))
    return doc


def binder(source, target_chain, minimum, maximum, internal):
    doc = document(source)
    retain(doc, doc.array.chain_id == target_chain)
    doc.reset_conditions()
    a = doc.array
    C_SEQ.set_annotation_from_ground_truth(a, np.ones(len(a), dtype=bool))
    if internal:
        set_internal_coordinate_conditioning(a, np.flatnonzero(np.isin(a.atom_name, BACKBONE)), True)
    doc.insert_segment(minimum, maximum, new_chain=True, chain_id="A", chain_type=ChainType.POLYPEPTIDE_L)
    sync_index_groups(doc.array, preserve_manual_edits=False)
    return doc


def save(doc, output):
    output.write_text("\n".join(line.rstrip() for line in doc.to_cif().splitlines()) + "\n")
    restored = document(output)
    compare_arrays(doc.array, restored.array)
    return restored


def build(pte, ptm, mart, bhrf1, output):
    output.mkdir(parents=True, exist_ok=True)
    prepared = {
        "inverse_folding.cif": (pte_inverse_folding(pte), pte),
        "enzyme_design.cif": (pte_scaffold(pte), pte),
        "ptm_binder.cif": (binder(ptm, "B", 150, 150, False), ptm),
        "protein_binder.cif": (binder(bhrf1, "B", 50, 120, True), bhrf1),
    }
    report = {}
    for name, (doc, source) in prepared.items():
        restored = save(doc, output / name)
        a = restored.array
        report[name] = {
            "source_file": source.name,
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "sha256": hashlib.sha256((output / name).read_bytes()).hexdigest(),
            "atoms": len(a),
            "sequence_atoms": int(C_SEQ.mask(a, default="generate").sum()),
            "absolute_coordinate_atoms": int(C_CRD.mask(a, default="generate").sum()),
            "internal_coordinate_atoms": int((C_CRDGRP.annotation(a, default="generate") > 0).sum()),
            "unindexed_residues": int(sum(~C_IDX.mask(a, default="generate")[get_residue_starts(a)])),
            "studio_validation": restored.validate(full_pipeline=True),
            "roundtrip": "atom identities, coordinates, bonds, charges and saved annotations match",
        }
    (output / "partial_diffusion.cif").write_bytes(mart.read_bytes())
    report["partial_diffusion.cif"] = {
        "source_file": mart.name,
        "sha256": hashlib.sha256(mart.read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(mart.read_bytes()).hexdigest(),
        "unchanged_source_bytes": True,
        "studio_validation": document(mart).validate(full_pipeline=True),
    }
    (output / "hit-example-provenance.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pte", "ptm", "mart", "bhrf1", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    build(**vars(parser.parse_args()))
