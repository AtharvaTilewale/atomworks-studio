# Building and extending AtomWorks Studio

This guide is for contributors building Studio, adding providers, or packaging
desktop integrations. For product usage, start with the
[main README](../README.md) or [browser guide](WEB.md).

## Prerequisites

Studio pins its development toolchain:

- Python 3.12.10 (`.python-version`)
- Node 22.22.0 (`.node-version`)
- pnpm 11.16.0 (`package.json`)
- Rust 1.97.1 with `wasm32-unknown-unknown` (`rust-toolchain.toml`)
- `uv`
- `wasm-pack` 0.13.1

The pinned AtomWorks and Patinae sources are fetched from GitHub. Configure Git
authentication before the first bootstrap if those repositories are private to
your account.

## Repository layout

- `packages/api` — FastAPI service, document sessions, and provider contracts
- `packages/web` — Vite PWA and Patinae/Mol* renderer adapters
- `packages/core` — lossless annotated-CIF inspector compiled to WASM
- `packages/vscode` — thin VS Code/Cursor webview launcher
- `packages/patinae-plugin` — thin Patinae desktop launcher
- `packages/pymol-plugin` — native PyMOL authoring interface
- `providers/rfd4` — optional RFD4 condition, validation, and design provider
- `vendor/patinae` — pinned Patinae source revision and reviewed patch series
- `deploy` — container, Kubernetes, local GPU, and Brev assets
- `tests` — API, provider, conformance, and cross-interface tests

The FastAPI service owns document truth. Browser and desktop clients use the
same API rather than maintaining separate copies of editor state. See
[Architecture](ARCHITECTURE.md) for the runtime boundaries.

## Bootstrap and daily loop

From the repository root:

```bash
make bootstrap
make run
```

`make bootstrap` verifies the committed Patinae viewer archive, installs the
pnpm and uv workspaces, and adds the Rust WASM target. `make run` rebuilds stale
web assets and starts Uvicorn on `127.0.0.1:8000` by default.

Useful overrides:

```bash
ANNOTATION_STUDIO_HOST=0.0.0.0 \
ANNOTATION_STUDIO_PORT=18000 \
  make run
```

Generated web, WASM, wheel, VSIX, cache, and acceptance outputs are ignored.
The checksum-pinned Patinae viewer tarball in `packages/web/vendor/` is the one
committed build artifact.

## Verification

Run the same top-level gates used by CI:

```bash
make lint
make typecheck
make test
make build
```

Provider conformance tests import RFD4 but do not require a GPU or model
weights. Put `RFD4-Proteina-dev` beside this repository or set:

```bash
export ANNOTATION_STUDIO_RFD4_ROOT=/path/to/RFD4-Proteina-dev
```

The test target uses `scripts/run_pytest.py` because embedded PyMOL shutdown
hooks can otherwise replace pytest's nonzero exit status. Do not bypass that
runner in CI or acceptance scripts.

Additional release gates and artifact locations are listed in
[Acceptance](ACCEPTANCE.md). The behavioral matrix across clients is in
[Desktop parity](PARITY.md).

## Build outputs

| Artifact | Command | Output |
|---|---|---|
| Browser/PWA | `pnpm --dir packages/web build` | `packages/web/dist/` |
| API and provider distributions | `uv build --all-packages` | `dist/` |
| VS Code/Cursor extension | `pnpm --dir packages/vscode package` | `packages/vscode/*.vsix` |
| Rust crates and Patinae launcher | `cargo build --workspace --release --locked` | `target/release/` |
| Full local build | `make build` | all of the above except VSIX packaging |
| CPU container | Docker command below | local image |

Build the production CPU image with a GitHub token available only as a BuildKit
secret:

```bash
export GH_TOKEN="$(gh auth token)"
docker build --secret id=github_token,env=GH_TOKEN \
  -f deploy/Dockerfile -t atomworks-studio:local .
```

Do not pass the token as a build argument or persist it in an image layer.

## Provider development

Provider-neutral protocols live in `annotation_studio.contracts`:

- `ConditionProvider` describes editable conditions and their value schemas.
- `ValidationProvider` validates a document against model-specific rules.
- `DesignProvider` submits, polls, and cancels generation jobs.

The API imports no model implementation in its document core. Optional provider
packages register through the `atomworks_studio.providers` Python entry-point
group; `providers/rfd4` is the reference implementation. Keep GPU runtimes,
credentials, retries, and worker scaling behind the `DesignProvider` boundary.

Start with the [API provider guide](../packages/api/README.md), then verify a
new provider with:

```bash
uv run python scripts/run_pytest.py -q tests/test_providers.py tests/test_design_jobs.py
```

If a provider adds browser-visible API fields, regenerate and commit the client
types:

```bash
bash scripts/generate_api_types.sh
git diff -- packages/web/src/generated/api.d.ts
```

## Client and extension development

### Website

```bash
pnpm --dir packages/web dev
pnpm --dir packages/web test:unit
```

The two renderers implement one adapter contract but never silently fall back
to each other. Test both modes through `packages/web/tests/smoke.mjs`.

Documentation screenshots are reproducible against a running service:

```bash
ANNOTATION_STUDIO_URL=http://127.0.0.1:8000 \
  node scripts/capture_docs_screenshots.mjs
```

### VS Code and Cursor

```bash
pnpm --dir packages/vscode compile
pnpm --dir packages/vscode package
cursor --install-extension packages/vscode/*.vsix --force
```

Reload the editor after reinstalling, configure a Studio server, choose a
renderer, and invoke **AtomWorks: Open Annotation Studio**. Use `code` instead
of `cursor` when testing VS Code.

### PyMOL

Create the supplied environment and install the plugin with its test extras:

```bash
cd packages/pymol-plugin
conda env create -f pymol_env.yaml
conda activate atomworks
python -m pip install -e ".[test]"
```

Run plugin and cross-interface conformance through `make accept-tools`.

### Patinae desktop

```bash
cargo build --release --locked -p patinae-annotation-studio
```

Install the platform library as described in the
[Patinae launcher guide](../packages/patinae-plugin/README.md). The launcher is
intentionally thin: it opens the configured Studio service in Patinae mode.

## Patinae viewer updates

The browser package is reproduced from the revision and patch series recorded
under `vendor/patinae`:

```bash
ATOMWORKS_STUDIO_REBUILD_PATINAE=1 \
  bash scripts/build_patinae_viewer.sh
```

The script builds in an external cache, normalizes host paths, and verifies the
result against the recorded SHA-256. Review [Source provenance](PROVENANCE.md)
before changing the pin, patches, package version, or checksum.

## Releases

`VERSION` is authoritative. JavaScript, Python, and Rust package versions must
match it; `python scripts/verify_distribution.py` enforces that contract.
Releases use `vX.Y.Z` tags. The tag workflow builds wheels, the VSIX, the
Patinae package, checksums, an SBOM, attestations, and the GHCR image.

```bash
python scripts/verify_distribution.py
git tag -a v0.1.0 -m "AtomWorks Studio 0.1.0"
git push origin v0.1.0
```

Production Kubernetes, local GPU, Brev, and AF3 operations are documented in
the [deployment runbook](../deploy/README.md). The repository currently ships
CI and tag-release workflows; Kubernetes application and rollout remain an
operator action unless an external deployment workflow is configured.
