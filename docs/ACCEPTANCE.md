# Acceptance

Run from a clean checkout:

```bash
make bootstrap
make accept-local
make accept-tools
```

First run the non-provisioning Brev preflight; it does not need a GPU or local
checkpoint directory:

```bash
ANNOTATION_STUDIO_SETUP_PREFLIGHT_ONLY=1 \
  bash deploy/brev/launchable-setup.sh
```

Full GPU acceptance requires `DATA_PATH` and the AF3 paths documented in
`deploy/brev/README.md`. `ANNOTATION_STUDIO_RFD4_ROOT` may override the bundled,
pinned RFD4 checkout:

```bash
make accept-brev
bash scripts/accept_brev_clean.sh
```

The clean check defaults to the `main` ref of the canonical Studio repository.
Before merge, select the branch or commit under review; use
`ATOMWORKS_STUDIO_CLEAN_REPOSITORY` as well when it lives in another clone:

```bash
ATOMWORKS_STUDIO_CLEAN_REF=<branch-or-commit> \
  bash scripts/accept_brev_clean.sh
```

The non-provisioning preflight initializes missing nested submodules, checks
every RFD4 source interface used by setup, and verifies both configured
checkpoint objects in the public bucket. It does not install packages, build an
image, start a service, or allocate a GPU.

Each script writes logs and machine-readable results under `artifacts/`.
`accept-brev` also writes `rfd4-assets.json`, proving that the checkpoint names
selected by pinned RFD4 are present locally and anonymously readable from the
public bucket with matching byte sizes.
Artifacts, credentials, model weights, generated viewer archives, build output,
and caches are never committed.

Release `v0.1.0` is blocked unless:

1. policy, static checks, unit/contract, integration, and packaging CI pass;
2. every row in `docs/PARITY.md` has passing evidence;
3. dual-renderer responsive and accessibility baselines pass;
4. the CPU image runs non-root and read-only;
5. existing-instance and clean-Launchable Brev cycles pass, including direct
   generation, multi-comparison, AF3 reward search, queue/restart behavior, and
   VSIX connection.

The accepted browser-shell budget is 80,000 bytes gzip, excluding separately
loaded Patinae and Mol* chunks. `scripts/check_web_budget.py` enforces it.
