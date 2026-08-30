# AtomWorks Annotation Studio API

For workspace bootstrap, verification, build outputs, and extension workflows,
see the [Studio development guide](../../docs/DEVELOPMENT.md). This file is the
API package and provider-boundary reference.

This directory is an independently installable Python distribution. Its version
must match `../../VERSION`; CI enforces one version across every Studio package.

The wheel contains:

- the FastAPI application and generic AtomWorks document core;
- stable `ConditionProvider`, `ValidationProvider`, and `DesignProvider`
  protocols;
- the production Vite build under `annotation_studio/web`.

Build the browser first, then the wheel:

```bash
pnpm --dir ../web build
uv build --project . --wheel
```

The Hatch build hook refuses to build a wheel without `../web/dist/index.html`
and copies the complete Vite output into the wheel. At runtime
`importlib.resources` serves packaged assets; a source checkout falls back to
`../web/dist`.

## Provider boundary

`annotation_studio.contracts` contains the model-neutral interfaces.
`annotation_studio.core` imports no RFD4 module and defaults to neutral
condition and validation providers. The separately installable
`atomworks-annotation-studio-rfd4` distribution contains all direct RFD4
imports. `annotation_studio.rfd4_provider` remains as a compatibility re-export,
and `main.py` is the composition root that selects those providers. A remote
deployment can replace that construction with an authenticated service
implementation of `DesignProvider`.

A custom design provider must not import a model implementation into the
web/API process. This keeps GPU runtimes, credentials, retries, and worker
scaling behind a process/service boundary while preserving the browser job
contract.

The shipped `ProcessDesignProvider` executes a configured command without a
shell for local and single-VM Brev deployments. Setting the durable database
and object-store variables selects `DurableDesignProvider`, shared PostgreSQL
sessions, and the leased `atomworks-studio-durable worker`; model execution
remains outside the web/API process. See `../../deploy/durable/README.md`.
