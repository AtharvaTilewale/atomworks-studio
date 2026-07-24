# Acceptance

Run from a clean checkout:

```bash
make bootstrap
make accept-local
make accept-tools
```

GPU acceptance requires `ANNOTATION_STUDIO_RFD4_ROOT`, `DATA_PATH`, and the AF3
paths documented in `deploy/brev/README.md`:

```bash
make accept-brev
bash scripts/accept_brev_clean.sh
```

Each script writes logs and machine-readable results under `artifacts/`.
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
