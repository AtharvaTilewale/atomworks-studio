# RFD4 Studio examples

These small annotated-CIF inputs exercise three representative RFD4 design
tasks through the same browser-to-GPU-to-browser path used in production:

- `ppi_pdl1.cif`: a 50-120 residue binder chain against the fixed PD-L1 target,
  with three hotspot residues. Source: `all_ppi.yaml:rfd3_pdl1`.
- `mcsa_ph2.cif`: a 180 residue enzyme scaffold around fixed MCSA catalytic
  residues and the PH2 ligand. Source: `mcsa_40_fixed_ligand.json:M0664_2dhn`.
- `disco_iai.cif`: a 150 residue de novo binder around the IAI ligand. Source:
  `sm_binder.json:IAI`; use the `disco_refolding` tag when AF3 is configured.

Their UI metric presets mirror the repository validation configs:

- PPI: `design`, coordinate/hotspot conditioning, interface interactions, and
  `ppi_refolding`.
- MCSA: `design`, `unindexed`, coordinate conditioning, and `ame_refolding`.
- DISCO: `design`, interface interactions, `sm_binder_refolding`, and
  `disco_refolding`.

AF3-backed tags produce confidence/interface metrics, refolded CIFs, RMSDs, and
task pass criteria. Opening a refolded result automatically colors available
pLDDT/confidence values on the structure.

Upload any file in Annotation Studio, then submit one design. A one-step job is
useful only as an end-to-end smoke test; use a normal diffusion step count for
structure quality.

The examples use compact expandable-segment sentinels rather than materializing
hundreds of generated atoms at placeholder coordinates. This preserves RFD4
semantics and renders cleanly in both Mol* and Patinae. Regenerate them from the
canonical repository inputs with:

```bash
export CLUSTER=local
export PYTHONPATH="$PWD/apps/annotation-studio/api:$PWD/src:$PWD/lib/atomworks/src"
.venv/bin/python apps/annotation-studio/scripts/build_examples.py
```

Local generation requires the two checkpoints checked by
`local/start.sh`:

```text
$DATA_PATH/latent_checkpoints/ldm_checkpoints/arch_sweep_2026_07_03_pair_trimult_every2_720k-ema.ckpt
$DATA_PATH/latent_checkpoints/ae_checkpoints/AE_ucond_512.ckpt
```

AF3-backed PPI, MCSA, and DISCO refolding metrics additionally require a
running AF3 daemon. They are intentionally not advertised by the Studio unless
all AF3 runtime assets are configured; see `../local/README.md`.
