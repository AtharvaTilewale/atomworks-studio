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
- `git` with access to the private `baker-laboratory` repositories

AtomWorks and RFD4-Proteina are vendored as git submodules under `external/`,
pinned to reviewed commits sourced from atomworks `dev` and RFD4 `main`. Clone
with `git clone --recursive`, or run `git submodule update --init --recursive`
before the first bootstrap. The patched Patinae viewer is a committed tarball.
Configure Git authentication first if the submodule repositories are private
to your account.

## Repository layout

- `packages/api` — FastAPI service, document sessions, and provider contracts
- `packages/web` — Vite PWA and Patinae/Mol* renderer adapters
- `packages/core` — lossless annotated-CIF inspector compiled to WASM
- `packages/vscode` — thin VS Code/Cursor webview launcher
- `packages/patinae-plugin` — thin Patinae desktop launcher
- `packages/pymol-plugin` — model-neutral PyMOL authoring interface (`atomworks_studio_pymol`)
- `packages/editing` — shared annotated-CIF editing primitives (`atomworks_studio_editing`)
- `providers/rfd4` — optional RFD4 condition/validation/design provider, plus the
  optional `[pymol]` extra with RFD4-specific PyMOL editors (`atomworks_studio_rfd4.pymol`)
- `external/` — the atomworks and RFD4-Proteina git submodules (the pinned dependencies)
- `vendor/patinae` — pinned Patinae source revision and reviewed patch series
- `deploy` — container, Kubernetes, local GPU, and Brev assets
- `tests` — API, provider, conformance, and cross-interface tests

The FastAPI service owns document truth for the browser, PWA, VS Code/Cursor,
and Patinae clients. The PyMOL plugin is a separate, fully local runtime:
`AtomArrayPlus` owns its in-process document state, and no website or API
service is required. See [Architecture](ARCHITECTURE.md) for the runtime
boundaries.

## Bootstrap and daily loop

From the repository root:

```bash
make bootstrap
make run
```

`make bootstrap` initializes the `external/` submodules, verifies the committed
Patinae viewer archive, installs the pnpm and uv workspaces (atomworks resolves
from its submodule via a uv path source), and adds the Rust WASM target. `make
run` rebuilds stale web assets and starts Uvicorn on `127.0.0.1:8000` by default.

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

Provider conformance tests import the RFD4 condition surface but do not require a
GPU or model weights; they use the `external/RFD4-Proteina-dev` submodule by
default. Point at a different checkout with:

```bash
export ANNOTATION_STUDIO_RFD4_ROOT=/path/to/RFD4-Proteina-dev
```

Tests that drive the RFD4 PyMOL editors or the full design pipeline need the
complete RFD4 model runtime and skip automatically when it is absent.

The test target uses `scripts/run_pytest.py` because embedded PyMOL shutdown
hooks can otherwise replace pytest's nonzero exit status. Do not bypass that
runner in CI or acceptance scripts.

Additional release gates and artifact locations are listed in
[Acceptance](ACCEPTANCE.md). The behavioral matrix across clients is in
[Desktop parity](PARITY.md).

RFD4 model assets are not vendored. Verify the exact files selected by the
pinned RFD4 config, plus their anonymous public-bucket copies, with:

```bash
DATA_PATH=/persistent/rfd4-data \
uv run python scripts/check_rfd4_assets.py \
  --rfd4-root external/RFD4-Proteina-dev --check-bucket
```

## Build outputs

| Artifact | Command | Output |
|---|---|---|
| Browser/PWA | `pnpm --dir packages/web build` | `packages/web/dist/` |
| API and provider distributions | `uv build --all-packages` | `dist/` |
| VS Code/Cursor extension | `pnpm --dir packages/vscode package` | `packages/vscode/*.vsix` |
| Rust crates and Patinae launcher | `cargo build --workspace --release --locked` | `target/release/` |
| Full local build | `make build` | all of the above except VSIX packaging |
| CPU container | Docker command below | local image |

Build the production CPU image directly from the recursive checkout:

```bash
docker build -f deploy/Dockerfile -t atomworks-studio:local .
```

## Provider development

Provider-neutral protocols live in `annotation_studio.contracts`:

- `ConditionProvider` describes editable conditions and their value schemas.
- `ValidationProvider` validates a document against model-specific rules.
- `JobProvider` parses, submits, polls, and cancels jobs and serves their
  outputs and artifacts (`DesignProvider` is its former name).

### StandardAnnotation editing contract

Semantic authoring metadata belongs on the upstream StandardAnnotation class,
not in a Studio-, PyMOL-, or YAML-specific registry:

- `editing_type` declares the writable value type; fixed-width vectors use a
  NumPy subarray dtype such as `np.dtype((np.float32, (3,)))`.
- `editing_is_mask_only` means readers may consume stored values but editors
  only change the mask. Sequence, coordinate, index, and internal-coordinate
  conditioning use this mode.
- `aliases` are accepted only when reading or resolving names. AtomWorks
  setters and CIF serialization write the canonical `full_name`/`mask_name`.

The RFD4 provider derives browser and PyMOL schemas from those fields. Studio
currently supports one-body masks, enums, integers, finite numeric scalars,
three-vectors, and the `C_DIS` distance-pair editor. Other pair types and
writable strings remain visible through stored-field inspection but are not
offered by an incompatible generic widget.

Internal-coordinate authoring stores only `C_INTCRD`/`C_CRD` masks and
`C_CRDGRP`; RFD4 featurization is the sole production path that centers,
randomly rotates, and writes `C_INTCRD` values.

The API imports no model implementation in its document core. Optional provider
packages register through the `atomworks_studio.providers` Python entry-point
group, and Studio loads all of them: one supplies the document (condition and
validation) providers, and any number add named job providers through
`create_job_provider`. `providers/rfd4` is the reference document provider; the
RFD4 design jobs are always registered as the job provider `rfd4`. A job
provider that runs a local command builds on
`annotation_studio.process_jobs.ProcessJobRunner`. Keep GPU runtimes,
credentials, retries, and worker scaling behind the `JobProvider` boundary.

Start with the [API provider guide](../packages/api/README.md), which lists the
provider exports, selection variables, and generic job routes, then verify a
new provider with:

```bash
PYTHONPATH=external/RFD4-Proteina-dev/src uv run python scripts/run_pytest.py -q \
  tests/test_providers.py tests/test_design_jobs.py tests/test_process_jobs.py tests/test_job_routes.py
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

Documentation screenshots are reproducible against a running service. Set
`CHROME_PATH` only when Chrome or Chromium is not installed in a standard
location. The checked-in documentation uses Mol*/WebGL because it renders
deterministically in headless CI; Patinae/WebGPU remains covered by its own
browser E2E path:

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

Create the supplied environment from the repository root and install the
workspace packages into the same Python that runs PyMOL:

```bash
git submodule update --init external/atomworks-dev
conda env create -f packages/pymol-plugin/pymol_env.yaml
conda activate atomworks
python -m pip install \
  -e external/atomworks-dev \
  -e packages/editing \
  -e "packages/pymol-plugin[test]"
```

Conda PyMOL 3.1 needs the minimal guarded `~/.pymolrc` shown in the
[PyMOL setup guide](../packages/pymol-plugin/README.md#start-pymol).
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
