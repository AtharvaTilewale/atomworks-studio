# NIM examples

Eight inputs from the private RFD4 NIM, revision
`3a8a1f6f2ed6bab37757d2471b5007971175225d`, under `nim/examples/*/request.json`.
The enzyme, motif, ligand binder, VHH, partial-diffusion and inverse-folding
CIF contents are unchanged. Antibody and unconditional condition specs were
converted with the native RFD4 condition-spec builder and standard-annotation
serializer. Antibody framework/target conditions and excluded CDR H3 are retained.

`examples.json` pairs each CIF with its sampling task. Partial diffusion uses
40% of the trajectory; inverse folding uses the native inverse-folding sampler.
The ZeroGPU demo uses the NIM 50-step, 170-trunk-pass recycle schedule and one
replicate per request. Example selection does not enable refolding or establish
biological design quality.

Keep this directory private with the deployment branch until release review.
