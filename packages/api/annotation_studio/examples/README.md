# Design examples

The bundled examples pair annotated CIFs with sampling tasks in `examples.json`.
Most originated from the private RFD4 NIM at revision
`3a8a1f6f2ed6bab37757d2471b5007971175225d`; source and preparation details are in
`docs/PROVENANCE.md`.

- **Antibody:** cropped MZB1 with the public adalimumab Fv framework from the
  historical antibody-campaign examples. Heavy/light framework backbones share
  one internal group; the target backbone is absolute-conditioned. All six CDRs
  are expandable segments. Three missing terminal atoms have free coordinates.
- **VHH:** PD-L1 with expandable CDRs. The VHH display coordinates are translated
  closer to the target; all conditions, including saved internal targets, remain
  unchanged.
- **Protein binder:** PD-L1 from RFD4 `all_ppi.yaml:rfd3_pdl1`, with a single
  internal group for its resolved N/CA/C/O backbone, six hotspot atoms on residues
  56, 115 and 123, and a separate 50–120 residue binder segment. It has no absolute
  coordinate conditioning; target sequence and index conditions remain fixed.
- **SAM:** positioned conformer from RFD4's interaction examples, with coordinate
  conditioning off.

Enzyme and VHH inputs include missing heavy atoms with free coordinates. Motif,
partial-diffusion and inverse-folding CIF contents are unchanged. Partial
diffusion uses 40% of the trajectory; inverse folding uses the native
inverse-folding sampler. The deployment chooses the native recycling preset and
design count. Example selection does not enable refolding or establish biological
design quality.
