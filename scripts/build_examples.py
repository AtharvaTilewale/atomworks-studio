#!/usr/bin/env python3
"""Build compact, viewer-safe Studio examples from canonical RFD4 inputs."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from annotation_studio.core import StudioDocument
from annotation_studio.rfd4_provider import CONDITION_PROVIDER, VALIDATION_PROVIDER

STUDIO_ROOT = Path(__file__).resolve().parents[1]
RFD4_ROOT = Path(os.environ["ANNOTATION_STUDIO_RFD4_ROOT"]).resolve()
OUTPUT = STUDIO_ROOT / "examples"


def document(path: Path, name: str) -> StudioDocument:
    return StudioDocument.from_cif(
        path.read_text(),
        name,
        condition_provider=CONDITION_PROVIDER,
        validation_provider=VALIDATION_PROVIDER,
    )


def write_ppi() -> None:
    model = document(
        RFD4_ROOT / "data" / "benchmarks" / "rfd3_ppi_paper_benchmark" / "5o45_cropped.cif",
        "ppi_pdl1",
    )
    target = list(range(len(model.array)))
    model.set_condition("sequence", target, enabled=True, scope="residue")
    model.set_condition("coordinate", target, enabled=True, scope="atom")
    hotspots = np.flatnonzero(
        ((model.array.res_id == 56) & np.isin(model.array.atom_name, ["CG", "OH"]))
        | ((model.array.res_id == 115) & np.isin(model.array.atom_name, ["CG", "SD"]))
        | ((model.array.res_id == 123) & np.isin(model.array.atom_name, ["CD2", "OH"]))
    ).tolist()
    model.set_condition("hotspot", hotspots, enabled=True, scope="atom")
    model.insert_segment(50, 120, new_chain=True, chain_id="B", chain_type="polypeptide(L)")
    (OUTPUT / "ppi_pdl1.cif").write_text(model.to_cif())


def write_mcsa() -> None:
    model = document(RFD4_ROOT / "data" / "benchmarks" / "mcsa_40" / "M0664_2dhn.cif", "mcsa_ph2")
    motif = np.flatnonzero(model.array.chain_id == "A").tolist()
    ligand = np.flatnonzero(model.array.chain_id == "B").tolist()
    model.set_condition("sequence", motif + ligand, enabled=True, scope="residue")
    fixed = np.flatnonzero(
        ((model.array.res_id == 22) & np.isin(model.array.atom_name, ["OE2", "CD"]))
        | ((model.array.res_id == 100) & np.isin(model.array.atom_name, ["NZ", "CE", "CD"]))
        | (model.array.chain_id == "B")
    ).tolist()
    model.set_condition("coordinate", fixed, enabled=True, scope="atom")
    model.set_condition("index", motif, enabled=False, scope="residue")
    model.insert_segment(180, 180, atom_index=0, before=True)
    (OUTPUT / "mcsa_ph2.cif").write_text(model.to_cif())


def write_disco() -> None:
    model = document(
        RFD4_ROOT
        / "data"
        / "benchmarks"
        / "rfbench"
        / "sm_binder"
        / "IAI_aa_ep10_run_binder_5sdv_cond0_0-atomized-bb-False.cif",
        "disco_iai",
    )
    model.set_condition("sequence", list(range(len(model.array))), enabled=True, scope="residue")
    model.insert_segment(150, 150, new_chain=True, chain_id="A", chain_type="polypeptide(L)")
    (OUTPUT / "disco_iai.cif").write_text(model.to_cif())


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_ppi()
    write_mcsa()
    write_disco()
    print(f"Wrote PPI, MCSA, and DISCO examples to {OUTPUT}")
