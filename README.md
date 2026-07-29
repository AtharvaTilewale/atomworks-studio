<p align="center">
  <img src="packages/web/public/atomworks_logo_dark.svg" width="500" alt="AtomWorks">
</p>

<h1 align="center">AtomWorks Studio</h1>

<p align="center"><em>Build, inspect, and run protein-design tasks as annotated CIFs.</em></p>

<p align="center">
  <a href="https://github.com/baker-laboratory/atomworks-studio/actions/workflows/ci.yaml"><img src="https://github.com/baker-laboratory/atomworks-studio/actions/workflows/ci.yaml/badge.svg" alt="CI"></a>
  <a href="VERSION"><img src="https://img.shields.io/badge/version-0.1.0-177f73" alt="Version 0.1.0"></a>
</p>

AtomWorks Studio is one editor shared by the website, VS Code/Cursor, PyMOL,
and Patinae. It reads and writes standard mmCIF files extended with AtomWorks
conditions, so design intent stays with the structure instead of being
transcribed into a separate JSON or YAML configuration.

The editor and all desktop integrations work on CPU. Connect the optional
RFD4-Proteina provider to generate designs, calculate metrics, compare outputs,
and run AF3 reward-guided search.

<p align="center">
  <img src="docs/images/web-molstar-editor.png" width="920" alt="AtomWorks Studio editing an annotated CIF with the Molstar renderer">
</p>

Two ways in:

- **Use Studio** → follow the [quick start](#quick-start), then choose the
  [website](docs/WEB.md), [PyMOL plugin](packages/pymol-plugin/README.md), or
  [VS Code/Cursor extension](packages/vscode/README.md).
- **Build or extend Studio** → start with the
  [development guide](docs/DEVELOPMENT.md) for repository layout, verification,
  artifacts, providers, and extension points.

---

## Three interfaces, one document

### Website and PWA

Use the complete editor in any supported browser. Patinae/WebGPU is the default
renderer; switch to Mol*/WebGL at any time. Load a CIF, edit conditions, inspect
interactions, and download the same annotated CIF.

[Use the browser editor →](docs/WEB.md)

### PyMOL plugin

Author annotated CIFs inside PyMOL with native panels for conditions, bonds,
charges, chains, and expandable segments. AtomWorks remains the authoritative
document model, so saved files round-trip into the website and RFD4.

[Install and use the PyMOL plugin →](packages/pymol-plugin/README.md)

### VS Code and Cursor extension

Open the shared Studio service in an editor webview. The extension defaults to
Patinae/WebGPU and only uses Mol*/WebGL if you select it. It stores only the
server URL and renderer choice; sessions and model data remain in the service.

<p align="center">
  <img src="docs/images/web-embedded.png" width="820" alt="AtomWorks Studio embedded as used by the VS Code and Cursor extension">
</p>

**Download the latest build** (rebuilt from `main` by CI on every push):
[`atomworks-annotation-studio.vsix`](https://github.com/baker-laboratory/atomworks-studio/releases/download/vscode-latest/atomworks-annotation-studio.vsix),
then `code --install-extension atomworks-annotation-studio.vsix --force` and
point it at a running Studio server.

[Install and set up the VS Code/Cursor extension →](packages/vscode/README.md)

---

## Quick start

You need Python 3.12, Node 22, pnpm 11, Rust, `uv`, and `wasm-pack`. The
[development guide](docs/DEVELOPMENT.md#prerequisites) lists the exact pinned
versions and authentication requirements.

AtomWorks and RFD4-Proteina are vendored as git submodules under `external/`, so
clone with submodules, then bootstrap and run:

```bash
git clone --recursive <repository-url>   # or run: git submodule update --init
make bootstrap
make run
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/); it opens with the default
renderer. Pin a specific renderer with a query parameter:

- [Patinae · WebGPU](http://127.0.0.1:8000/?viewer=patinae) (default)
- [Mol* · WebGL](http://127.0.0.1:8000/?viewer=molstar)

The CPU service is enough to edit, validate, and round-trip annotated CIFs. See
the [browser guide](docs/WEB.md) for the first workflow and the
[example designs](examples/README.md) for PPI, enzyme, and small-molecule
starting points.

## Add RFD4 generation

RFD4-Proteina is bundled as the `external/RFD4-Proteina-dev` submodule. Run its
one-time model setup, point `DATA_PATH` at the checkpoints, and start the local
GPU launcher (set `ANNOTATION_STUDIO_RFD4_ROOT` only to override the bundled
submodule with a separate checkout):

```bash
export DATA_PATH=/persistent/rfd4-data
bash deploy/local/start.sh
```

Use the [local GPU guide](deploy/local/README.md) for a workstation or the
[Brev guide](deploy/brev/README.md) for an authenticated GPU deployment. The
provider is optional: when it is absent, Studio remains a fully functional CPU
editor.

## Documentation

- [Using the website and PWA](docs/WEB.md)
- [Using the PyMOL plugin](packages/pymol-plugin/README.md)
- [Using the VS Code/Cursor extension](packages/vscode/README.md)
- [Using the Patinae desktop launcher](packages/patinae-plugin/README.md)
- [Building and extending Studio](docs/DEVELOPMENT.md)
- [Example annotated CIFs](examples/README.md)
- [Production deployment runbook](deploy/README.md)
- [Authenticated Brev pilot status](deploy/brev/PILOT_STATUS.md)
- [Architecture](docs/ARCHITECTURE.md) and [feature parity](docs/PARITY.md)
- [Source provenance](docs/PROVENANCE.md)

AtomWorks, RFD4-Proteina, and Patinae remain revision-pinned upstream
dependencies. Use of this repository is governed by the
[NVIDIA Source Code License](LICENSE.md).
