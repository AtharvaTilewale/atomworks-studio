# Example structures

The **Edit → Examples** menu uses the packaged NIM catalog in
`packages/api/annotation_studio/examples/`. These eight inputs also power the
Gradio demo; model execution is optional.

The files below remain regression fixtures and can be opened with **Open CIF**:

- `ppi_pdl1.cif`: PD-L1 target with three hotspot residues and a 50–120 residue
  expandable segment. Source: RFD4 `all_ppi.yaml:rfd3_pdl1`.
- `mcsa_ph2.cif`: catalytic residues and PH2 ligand with a 180 residue expandable
  segment. Source: RFD4 `mcsa_40_fixed_ligand.json:M0664_2dhn`.
- `disco_iai.cif`: IAI ligand with a 150 residue expandable segment.
  Source: RFD4 `sm_binder.json:IAI`.

The files illustrate stored annotations, ligand editing, and expandable segments.
They open in browser, VS Code, and PyMOL without a model runtime.
