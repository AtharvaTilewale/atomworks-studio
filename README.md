# AtomWorks Annotation Studio

Annotated-CIF authoring for PyMOL, Mol*, Patinae, browsers, VS Code, and
installable desktop workflows.

For DNS, Kubernetes, GHCR, GitHub environment, Brev, warm AF3 daemon, search,
rollback, and production acceptance steps, see the
[production runbook](deploy/README.md).
For a browser-to-RFD4 demo on one GPU workstation, see the
[local prototype guide](local/README.md).

## Architecture

- There are two mutation backends, not one per viewer. The PyMOL plugin keeps
  an authoritative `AtomArrayPlus` in its process. Annotation Studio keeps the
  same native AtomWorks type in its Python API session.
- **Mol\* 5.11.0** and **Patinae 0.4.4** are thin rendering/selection adapters
  around one browser UI. They are independent, equal options:
  `?viewer=patinae` selects Patinae/WebGPU and `?viewer=molstar` selects
  Mol*/WebGL. The application never silently switches renderers.
- **`core/`** is a small Rust/WASM CIF inspector. It validates `_atom_site`,
  inventories AtomWorks categories, and proves byte-exact pass-through of
  unknown/custom CIF data. It is deliberately not a third mutation backend.
- **`api/`** uses native AtomWorks/RFD4 Python as the authority for every
  mutation and CIF export. This avoids a second, subtly different
  implementation of condition defaults, groups, expandable segments, bonds,
  and validation. Each anonymous session retains its authoritative
  `AtomArrayPlus` in memory, analogous to the PyMOL plugin's background state.
- **`api/pyproject.toml`** builds the independently versioned
  `atomworks-annotation-studio` wheel. Its provider-neutral core depends on
  explicit `ConditionProvider`, `ValidationProvider`, and `DesignProvider`
  contracts; all direct RFD4 imports live in `rfd4_provider.py`. The wheel
  bundles the built browser application and serves it through package
  resources.
- **`web/`** is the only non-PyMOL UI implementation. Browser, installable
  desktop PWA, NIM iframe, and VS Code all load this same build.
- **`web/src/viewers/`** is the renderer boundary: `contract.ts` owns the small
  shared interface and annotation palette, `molstar.ts` and `patinae.ts`
  contain vendor-specific calls, and `index.ts` owns explicit lazy loading.
  The adapters are separate build chunks, so a client downloads only the
  renderer it uses.
- **`vscode/`** and **`patinae-plugin/`** are intentionally tiny launchers.
  They choose a URL/viewer mode; they contain no condition or editing logic.
- **`api/annotation_studio/design_jobs.py`** is the optional GPU boundary. It
  owns a provider-neutral job record and argv-only process provider; renderers,
  editor mutations, and model execution do not import each other. Remote GPU
  implementations use the same `DesignProvider` contract over a service/queue
  rather than importing model code into the API.

The Patinae README describes `@patinae/viewer` as an npm package, but it is not
present in the npm registry. The web app therefore pins the official v0.4.4
`patinae-web.tar.gz` release asset. Mol* is also pinned exactly because the
adapter intentionally uses its documented `Viewer`, `StructureElement.Loci`,
interactivity-manager, and structure-overpaint APIs; forced-Mol* browser tests
guard that integration before an upgrade is merged.

## Supported operations

- Sequence, coordinate, index/unindex, hotspot, RASA, hydrogen-bond, index
  group, coordinate group, and chain-centroid conditions.
- Clickable annotation summaries, per-atom annotation inspection, and
  condition highlighting in the active molecular viewer using
  AtomWorks-authoritative atom sets.
- A complete inventory of every serialized `mask_*`, `condition_*`, and
  `annotation_*` field. Unknown future fields remain visible and inspectable
  even before a dedicated editor is added.
- Atom, residue, and chain selection scopes.
- Formal charges and manual bond add/remove.
- Atom deletion, residue-ID reset, expandable segment insert/realize/collapse,
  undo, and document combination through both the browser and API.
- A chain-aware sequence strip that marks sequence, coordinate, and hotspot
  conditions; residue tokens select their atoms, while `[min–max]` segment
  tokens expand to realized `X` residues and collapse back in one click.
- Native annotated-CIF export and full RFD4 design-pipeline validation.
- Direct RFD4 generation or reward-guided search from the current in-memory
  CIF, ranked input/output switching, per-design metrics, and pLDDT/confidence
  coloring when an oracle/refolded CIF carries that atom-level field.

## Run

Prerequisites: Rust with `wasm32-unknown-unknown`, `wasm-pack`, pnpm 11, and
Node 20.19+.

```bash
./apps/annotation-studio/run.sh
```

Open `http://127.0.0.1:8000`.

The launcher installs/builds the web assets when needed and then starts the
native AtomWorks API. Set `ANNOTATION_STUDIO_HOST` or
`ANNOTATION_STUDIO_PORT` to override the default bind address and port.

### Browser renderer options

- `http://127.0.0.1:8000/?viewer=patinae` — Patinae/WebGPU.
- `http://127.0.0.1:8000/?viewer=molstar` — Mol*/WebGL.

The **Quick-start examples** panel bundles three canonical annotated CIFs into
the web build: a hotspot-conditioned PD-L1 PPI binder, an MCSA active-site
scaffold with sequence/coordinate conditions, and a DISCO-style IAI ligand
binder. Loading a card creates a normal editable Studio session and selects the
task's available metric tags. AF3-backed task metrics are selected only when
the deployment advertises them. Source specifications and regeneration
commands are in [`examples/README.md`](examples/README.md).

Generation starts with the inexpensive `design` metric group. Search separately
defaults its reward group to `interchain_interactions` and maximizes
`interchain_interactions.hbond_atom_pair_count_coi`; selecting output metric
groups does not accidentally run them during every beam rollout. Quick starts
also set task-aware search rewards: PPI/DISCO use interface hydrogen bonds,
while MCSA minimizes `clash_metrics.frac_atoms_clashing_1p5A`. The reward group,
metric key, and weight remain editable, including AF3 `oracle.ranking_score`
search when unconditional refolding is available. Chain-specific AF3 rewards
such as `oracle.plddt_coi` require a chain-of-interest annotation.

These are modes of the same application, not separate Patinae and Mol*
implementations. Upload, editing, annotation summaries, export, and API calls
therefore cannot drift between them. With no `viewer` parameter, first-time
users must choose one; the site remembers that explicit choice and always
offers the renderer selector. A renderer error is reported instead of invoking
the other adapter.

Mol* loads mmCIF text directly with `Viewer.loadStructureFromData`. AtomWorks
atom indices map to Mol* `atom.sourceIndex`; picks map back through the same
property. Clickable annotations use `StructureElement.Schema` to build exact
atom loci, validate their cardinality, apply persistent per-group overpaint,
select the union, and focus the camera. Clearing removes overpaint, selection,
hover, and focus state without reparsing the CIF. Patinae implements the same
contract with its command API and reparses only when it must reset colors.

### VS Code

```bash
cd apps/annotation-studio/vscode
pnpm install
pnpm package
code --install-extension atomworks-annotation-studio-0.1.0.vsix
```

With the service running, invoke **AtomWorks: Open Annotation Studio**. Use
**AtomWorks: Configure Annotation Studio Server** (or
`annotationStudio.serverUrl`) to select the permanent HTTPS service. Set
`annotationStudio.viewerBackend` to `patinae` or `molstar`, or use
**AtomWorks: Select Annotation Studio Renderer**. The extension prompts when
the setting is absent.

The packaged extension is a thin client for the same hosted web/API service;
it does not bundle a platform-specific Python environment. Pointing its
`serverUrl` at a production deployment gives users a one-install experience.
Tagged `annotation-studio-vX.Y.Z` releases attach the VSIX to GitHub and
optionally publish it to Visual Studio Marketplace and Open VSX when their
repository tokens are configured.

### Desktop app

The lowest-maintenance desktop distribution is the installable PWA served by
the website. In Chrome or Edge, open the HTTPS deployment and choose **Install
AtomWorks Annotation Studio**. The installed window runs the exact same assets
and API as the browser and VS Code versions; the service worker caches only the
app shell, never API/session responses.

Mol* is a web framework rather than a separate native desktop application, so
the PWA and VS Code shell are its desktop integrations. Both can force
`viewer=molstar`; an Electron-specific Mol* wrapper would duplicate packaging
without adding editor functionality.

Patinae 0.4.4 also has a native Rust plugin SDK. The small launcher plugin in
`patinae-plugin/` adds an `annotation_studio` command to Patinae desktop without
duplicating the editor:

```bash
cargo build --release --locked \
  --manifest-path apps/annotation-studio/patinae-plugin/Cargo.toml
mkdir -p ~/.patinae/plugins
cp apps/annotation-studio/patinae-plugin/target/release/libpatinae_annotation_studio.* \
  ~/.patinae/plugins/

ANNOTATION_STUDIO_URL=https://studio.example.com patinae
# In the Patinae command line:
annotation_studio
```

The launcher explicitly opens the shared client with Patinae selected. It
intentionally
does not reimplement AtomWorks mutations inside Patinae's Rust process.
Transferring a native Patinae selection directly would require a separate
annotated-CIF/IPC bridge and is not necessary for opening and editing files.

## NIM integration

A NIM should not embed model inference or an NGC key in this browser bundle.
The NIM application backend should call its GPU NIM, receive the predicted CIF,
then hand that CIF to the CPU Annotation Studio API:

```bash
curl -sS -F file=@prediction.cif \
  https://studio.example.com/api/sessions
```

The response includes `session_id`, `structure`, and a relative `editor_path`
such as `/?session=...&embed=1`. A build.nvidia.com-style application must add
either `viewer=patinae` or `viewer=molstar`, then redirect to that path or place
it in an iframe. If the parent needs lifecycle notifications, append its exact
encoded origin:

```text
https://studio.example.com/?session=...&embed=1&viewer=patinae&parent_origin=https%3A%2F%2Fbuild.nvidia.com
```

The frame posts `atomworks-annotation-studio` `ready` and `changed` messages
containing the session ID to that exact origin. On `changed`, the NIM backend
can retrieve the authoritative result from
`GET /api/sessions/{session_id}/cif`. This keeps model credentials and
cross-service traffic server-side and requires no permissive browser CORS.

For an embedded production deployment:

- put both services behind the same identity-aware gateway;
- configure `ANNOTATION_STUDIO_FRAME_ANCESTORS` to only the approved parent
  origins, for example `'self' https://build.nvidia.com vscode-webview:` when
  the hosted VS Code extension is also supported;
- treat the random session ID as a short-lived capability and do not log it;
- keep inference on GPU nodes and Annotation Studio on CPU nodes.

## RFD4 generation and Brev

Brev is a GPU environment provider, not the job protocol. The lowest-maintenance
Brev deployment runs this same web/API process beside RFD4 on one authenticated
instance. The API writes the current authoritative CIF into a capability-scoped
job directory, executes `generate.py` directly (no SLURM), reads its generated
CIFs and `per_example_metrics` CSV, and returns lazy editor sessions for each
result. Only an opened output consumes a Studio session.

```bash
export DATA_PATH=/persistent/rfd4-data
bash apps/annotation-studio/brev/start.sh
```

Open Brev's authenticated URL for port 8000. Full setup and oracle caveats are
in [`brev/README.md`](brev/README.md); the Launchable builder setup script is
[`brev/launchable-setup.sh`](brev/launchable-setup.sh).

The **Generate with RFD4** panel supports vanilla generation and beam search,
including design count, batch size, diffusion steps, checkpoints, branching,
beam width, metric tags, reward key/weight, and an advanced YAML options file.
Completed jobs expose the submitted input, every generated design, and every
AF3-refolded CIF in one result strip. Use Previous/Next to cycle through them or
Align to input to perform a common-backbone Kabsch alignment and report the
before/after RMSD. Important pLDDT, ipTM/ipAE, RMSD, ddG/TMol, pass-criterion,
and search-reward values are shown first, with the remaining numeric metrics in
an expandable list. Refolded structures automatically color available per-atom
pLDDT/confidence values.
Uploaded YAML is intentionally not a raw Hydra program: it may tune
`generation`, `search`, metrics, seed, batch/replicate counts, and checkpoint
filename, but it cannot replace input/output paths, defaults, interpolation, or
`_target_` objects. The server adds the one allowed beam-search target itself.

Set these variables when enabling another same-host GPU deployment:

- `ANNOTATION_STUDIO_JOB_DIR` — persistent job/output directory; enables job
  storage.
- `ANNOTATION_STUDIO_RFD4_ROOT` — checked-out RFD4 repository root.
- `ANNOTATION_STUDIO_RFD4_COMMAND` — argv-style command prefix for
  `generate.py`; no shell is used.
- `ANNOTATION_STUDIO_MAX_ACTIVE_JOBS` — running plus queued jobs (default 4).
- `ANNOTATION_STUDIO_JOB_TIMEOUT_SECONDS` — per-job timeout (default 4 hours).
- `ANNOTATION_STUDIO_JOB_RETENTION_SECONDS` — completed-job retention (default
  7 days; minimum 1 hour).

The shipped public Kubernetes manifest deliberately leaves these unset. Its CPU
pod remains the permanent editor. A production NIM can implement the same job
contract behind an authenticated GPU service; Brev's local provider is the
reproducible interactive path.

## CI and deployment

GitHub-hosted and self-hosted Actions runners are ephemeral CI workers. They
must not run the public server after a job exits. Their role here is to test,
publish an immutable container to GHCR, apply the Kubernetes manifest, and
verify the public HTTPS endpoint.

`.github/workflows/annotation-studio.yaml` rebuilds the website, tests the Rust
CIF core, tests both browser render paths, packages the VS Code extension,
builds the Patinae desktop plugin for Linux/macOS/Windows, and uploads the
artifacts for every relevant pull request and `main` update. It also builds and
boots the actual CPU container on pull requests, preventing a working static
build from hiding a broken runtime image. On `main`, it publishes SHA and
`latest` image tags to private GHCR. The SHA tag is the deployment authority;
`latest` is only a convenience pointer.

The repository already has a `main`-only `annotation-studio` GitHub environment
with safe defaults and deployment disabled. Complete it with:

- repository variable `ANNOTATION_STUDIO_DEPLOY_ENABLED`: `true`
- secret `KUBE_CONFIG_B64`: base64-encoded, least-privilege deploy kubeconfig
- secret `GHCR_PULL_TOKEN`: long-lived fine-grained token with `read:packages`
- variable `GHCR_PULL_USERNAME`: owner of that package token
- variable `ANNOTATION_STUDIO_NAMESPACE`: pre-created target namespace
- variable `ANNOTATION_STUDIO_HOST`: public DNS name
- variable `ANNOTATION_STUDIO_INGRESS_CLASS`: cluster ingress class
- variable `ANNOTATION_STUDIO_CERT_ISSUER`: cert-manager cluster issuer
- variable `ANNOTATION_STUDIO_TLS_SECRET`: cert-manager TLS secret name
- variable `ANNOTATION_STUDIO_FRAME_ANCESTORS`: CSP allowlist for iframe hosts

Before enabling deployment, create the namespace, install an ingress controller
and cert-manager, and point the host's DNS record at the ingress load balancer.
Merges to `main` then publish the SHA-tagged image, deploy it, wait for the
rollout, and test both `/api/health` and the PWA manifest over public HTTPS.
The health check must report the exact deployed Git revision. A failed rollout
automatically restores the previous Deployment revision.

### Versioned releases

`apps/annotation-studio/VERSION` is the release authority. Keep the `version`
in both `web/package.json` and `vscode/package.json` equal to it, then create an
annotated tag:

```bash
git tag -a annotation-studio-v0.1.0 -m "Annotation Studio 0.1.0"
git push origin annotation-studio-v0.1.0
```

The tag must exactly match `annotation-studio-v<VERSION>`. CI publishes the
semver container tag, creates/updates a GitHub release with checksummed web and
VSIX artifacts plus all Patinae desktop libraries, and optionally publishes the
VSIX to extension registries:

- `VSCE_PAT` — Visual Studio Marketplace token authorized for the `nvidia`
  publisher.
- `OVSX_PAT` — Open VSX token authorized for the same namespace.

If either token is absent, that registry is skipped while the GitHub release
still succeeds. The web tarball is an inspection/offline-shell artifact; it is
not a replacement for the same-origin FastAPI container.

The production manifest in `deploy/kubernetes.yaml` uses one CPU replica,
private-registry credentials, TLS-only ingress, upload/rate limits, a
read-only root filesystem, startup/readiness/liveness probes, and a 5 GiB
persistent volume. Active CIF state survives pod replacement for the normal
two-hour session lifetime. Undo history remains process-local and resets after
a pod restart. `Recreate` avoids attaching the RWO volume to two pods or
routing one session to two authorities.

Rollback to any known image without rebuilding by setting
`ANNOTATION_STUDIO_IMAGE` to its immutable SHA tag and applying the manifest,
or use `kubectl rollout undo deployment/annotation-studio` in the configured
namespace.

GitHub Pages can host the static `web/dist` artifact, but it cannot run the
FastAPI service or retain authoritative `AtomArrayPlus` sessions. The permanent
site is therefore one same-origin Kubernetes service containing the built site
and Python API. Scaling beyond one replica requires replacing the PVC snapshot
store with a shared transactional store and distributed mutation locking.

WebGPU/WebGL rendering runs on each user's browser GPU. The permanent annotation
server does not need a GPU. Integrated generation is enabled only in the
authenticated Brev/same-host topology above; a multi-user production site
should put RFD4 behind a dedicated GPU NIM/service.

## Verify

```bash
cargo test --manifest-path apps/annotation-studio/core/Cargo.toml
cargo test --locked \
  --manifest-path apps/annotation-studio/patinae-plugin/Cargo.toml

PYTHONPATH=apps/annotation-studio/api \
  uv run --with fastapi --with python-multipart --with pyyaml --with httpx2 \
  pytest -q apps/annotation-studio/tests/test_core_conformance.py \
    apps/annotation-studio/tests/test_design_jobs.py \
    apps/annotation-studio/tests/test_api.py

cd lib/atomworks
uv sync --all-groups
uv pip install --python .venv/bin/python pymol-open-source
.venv/bin/pytest -q src/atomworks/pymol_plugin/tests
cd ../..

PYTHONPATH=apps/annotation-studio/api \
  uv run --with fastapi --with python-multipart --with httpx2 \
    --with pymol-open-source \
  pytest -q apps/annotation-studio/tests/test_pymol_conformance.py

cd apps/annotation-studio/web
VIEWER_MODE=patinae pnpm test:e2e
VIEWER_MODE=molstar pnpm test:e2e
```

The browser test uses `/usr/bin/google-chrome`, requires Mol* in both automatic
and explicit modes, uploads and combines real annotated CIFs, verifies
annotation loci cardinality, and clears persistent colors. Forced Patinae mode
requires a host/browser with working WebGPU and remains an opt-in manual check.

## Maintaining a renderer

Keep parsing, mutation, annotation definitions, and UI controls outside
`web/src/viewers/`. A renderer implements only the contract in `contract.ts`:
initialize, load CIF, map atom picks, display AtomWorks atom-index groups,
clear display state, and dispose. New renderer dependencies must be lazy
imports in `viewers/index.ts`.

When upgrading Mol* or Patinae, keep the version exact, run `pnpm build`, then
run the browser smoke test with the renderer forced. A renderer must reject an
atom-index cardinality mismatch instead of displaying a plausible but
incorrect selection. This is the main safeguard against upstream indexing or
selection API changes.

## Adding a condition

Interactive condition metadata lives in one UI-agnostic registry:
`src/rfproteina/condition_specs.py`. Standard mask, enum, integer-group, and
vector conditions added there automatically drive:

- the PyMOL plugin's editor, summary, and display registration;
- Annotation Studio's API schema, mutation behavior, summaries, and browser
  controls.

Patinae and Mol* never receive condition-specific editor code; both consume the
same AtomWorks atom indices through `web/src/viewers/contract.ts`. VS Code, the
PWA, NIM embedding, and the Patinae launcher all load that same browser code.
This is the intended synchronization boundary.

Only conditions requiring genuinely custom behavior need a PyMOL or browser
override. Regardless, serialized fields are always shown by the generic field
inventory, preventing new annotations from silently disappearing.

## Service limits

Uploads are CIF/mmCIF only and capped at 32 MiB; model-options YAML is capped at
128 KiB. Anonymous session IDs use 192
bits of entropy, expire after two hours of inactivity, retain at most ten undo
snapshots, and are pruned at 64 concurrent sessions. Kubernetes persists the
latest CIF snapshot, but not undo history, on encrypted-at-rest cluster storage.
GPU jobs are capability-scoped, queue four at a time by default, execute without
a shell, and remain on persistent Brev storage. Use a shared transactional store
and external GPU queue before running multiple workers or replicas.
