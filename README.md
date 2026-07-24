# AtomWorks Studio

AtomWorks Studio is the shared annotated-CIF editor for the browser, PWA,
VS Code, Patinae, and PyMOL. The editor works without a GPU. An optional
RFD4-Proteina provider adds generation, metrics, comparison, and AF3
reward-guided search.

This is the product repository. AtomWorks, RFD4-Proteina, and Patinae remain
pinned upstream dependencies; provenance is recorded in
[`docs/PROVENANCE.md`](docs/PROVENANCE.md).

## Repository

- `packages/api` — FastAPI editor and in-process session authority
- `packages/web` — Vite PWA with Patinae and Mol* adapters
- `packages/core` — lossless annotated-CIF WASM inspector
- `packages/vscode` — thin URL-launching extension
- `packages/patinae-plugin` — native Patinae desktop launcher
- `packages/pymol-plugin` — native PyMOL Studio UI
- `providers/rfd4` — optional condition, validation, and design provider
- `vendor/patinae` — exact upstream revision plus reviewed patch series
- `deploy` — CPU image, local GPU, Brev, and Kubernetes launchers

## Bootstrap and CPU editor

Prerequisites are Python 3.12, Node 22, pnpm 11, stable Rust, `uv`, and
`wasm-pack`.

```bash
make bootstrap
make test
make run
```

Open <http://127.0.0.1:8000>. To run the packaged CPU image:

```bash
export GH_TOKEN="$(gh auth token)"
docker build --secret id=github_token,env=GH_TOKEN \
  -f deploy/Dockerfile -t atomworks-studio:local .
docker run --rm --read-only --tmpfs /tmp --tmpfs /app/state \
  -p 8000:8000 atomworks-studio:local
```

## RFD4 and AF3

Keep RFD4 as a sibling or external checkout and point Studio to it explicitly:

```bash
export ANNOTATION_STUDIO_RFD4_ROOT=/path/to/RFD4-Proteina-dev
export DATA_PATH=/persistent/rfd4-data
bash deploy/local/start.sh
```

The launcher sources RFD4’s `scripts/inference_runtime.sh`, starts exactly one
AF3 daemon when configured, shares `/tmp` with GPU subprocesses, and stores
compiler, job, and session caches outside the repository. See
[`deploy/local/README.md`](deploy/local/README.md) and
[`deploy/brev/README.md`](deploy/brev/README.md).

## Desktop integrations

VS Code:

```bash
pnpm --dir packages/vscode package
code --install-extension packages/vscode/*.vsix
```

PyMOL:

```bash
uv pip install packages/pymol-plugin
```

Patinae:

```bash
cargo build --release --locked -p patinae-annotation-studio
mkdir -p ~/.patinae/plugins
cp target/release/libpatinae_annotation_studio.* ~/.patinae/plugins/
```

All desktop clients launch or synchronize with the same Studio service; they do
not duplicate browser state.

## Acceptance

```bash
make accept-local
make accept-tools
make accept-brev
bash scripts/accept_brev_clean.sh
```

The desktop parity matrix is in [`docs/PARITY.md`](docs/PARITY.md). Release
gates, artifact collection, security, accessibility, and performance rules are
in [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md).

## Releases

`VERSION` is authoritative and must match all package versions. Releases use
`vX.Y.Z` tags. Generated WASM, Vite output, VSIX files, wheels, patched viewer
archives, model weights, credentials, and acceptance artifacts are never
committed.
