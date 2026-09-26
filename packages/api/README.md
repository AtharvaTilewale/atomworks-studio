# AtomWorks Annotation Studio API

For workspace bootstrap, verification, build outputs, and extension workflows,
see the [Studio development guide](../../docs/DEVELOPMENT.md). This file is the
API package and provider-boundary reference.

This directory is an independently installable Python distribution. Its version
must match `../../VERSION`; CI enforces one version across every Studio package.

The wheel contains:

- the FastAPI application and generic AtomWorks document core;
- stable `ConditionProvider`, `ValidationProvider`, and `JobProvider`
  protocols (`DesignProvider` remains an alias of `JobProvider`);
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
implementation of `JobProvider`.

A custom job provider must not import a model implementation into the
web/API process. This keeps GPU runtimes, credentials, retries, and worker
scaling behind a process/service boundary while preserving the browser job
contract.

The shipped `ProcessDesignProvider` executes a configured command without a
shell for local and single-VM Brev deployments. Setting the durable database
and object-store variables selects `DurableDesignProvider`, shared PostgreSQL
sessions, and the leased `atomworks-studio-durable worker`; model execution
remains outside the web/API process. See `../../deploy/durable/README.md`.

## Providers

`annotation_studio.providers` loads every module registered under the
`atomworks_studio.providers` entry-point group, sorted by entry-point name. A
provider module may export:

- `CONDITION_PROVIDER`, and optionally `VALIDATION_PROVIDER` and
  `CONDITION_OPERATIONS`: the document providers of every session;
- `create_job_provider(project_root: Path) -> JobProvider | None`: a job
  provider registered under the entry-point name;
- `PROVIDER_LABEL`: the display name, for example `"AtomWorks-Quantum"`.

Exactly one module supplies the document providers:
`ANNOTATION_STUDIO_CONDITION_PROVIDER=<name>` if set (startup fails when that
provider is missing, failed to load, or exports no `CONDITION_PROVIDER`),
otherwise the first module that exports `CONDITION_PROVIDER`, otherwise the
neutral providers. A provider that fails to import is skipped; one whose
`create_job_provider` raises `ImportError`, `OSError`, or `RuntimeError` keeps
its document providers but has no job provider (listed with `jobs: false`).
Both are logged as warnings; set `ANNOTATION_STUDIO_PROVIDER_REQUIRED=1` to
make them fatal.

The RFD4 design jobs are always registered as the job provider `rfd4`, built
exactly as before: `DurableDesignProvider` when the durable database is
configured, otherwise `ProcessDesignProvider`. Durable mode applies only to
RFD4; other job providers run through their own process runner on the API host,
so run a single API replica when you enable them. `providers/quantum` (extra
`quantum`) is a job-only provider that runs atomworks-quantum out of process;
see `../../providers/quantum/README.md`.

A `JobProvider` adds `parse_request` (raise `ValueError` for an invalid
payload) and `artifact` (return a file and its media type; raise `KeyError`
when absent) to the submit, poll, cancel, and output methods; `schema()`
includes at least `enabled`, `provider`, and `label`. Every job route exists
under `/api` and `/api/v1`:

| Route | Purpose |
|---|---|
| `GET /api/providers` | `name`, `label`, `enabled`, `conditions`, `jobs` of each provider |
| `GET /api/providers/{name}/jobs/schema` | The provider's `schema()` |
| `POST /api/sessions/{session_id}/providers/{name}/jobs` | Parse the JSON body and submit the session structure |
| `GET /api/providers/{name}/jobs/{job_id}` | Poll a job |
| `POST /api/providers/{name}/jobs/{job_id}/cancel` | Cancel a job |
| `POST /api/providers/{name}/jobs/{job_id}/outputs/{output_id}/open` | Open an output as an editor session |
| `POST /api/providers/{name}/jobs/{job_id}/outputs/{output_id}/align-to-input` | Open an output aligned to the job input |
| `GET /api/providers/{name}/jobs/{job_id}/outputs/{output_id}/artifacts/{artifact}` | Download an output file |

Unknown providers, jobs, outputs, and artifacts return 404. A submission is
parsed only after its session (404) and `Idempotency-Key` header (400) check
out; then idempotency conflicts map to 409, full queues to 429, unavailable
runtimes (`RuntimeError`) to 503, and invalid requests (`ValueError`) to 422. Job
responses list each output's `artifacts` as `{name, label}` pairs and never
expose server paths. The `/api/design/...` and `/api/design-jobs/...` routes
remain as the `rfd4` provider's routes.

`annotation_studio.process_jobs.ProcessJobRunner` implements everything except
`schema` and `parse_request` for providers that run one command per job;
`ProcessDesignProvider` is the runner plus RFD4 hooks. A provider supplies
`ProcessJobHooks`: the job-id prefix, request files, request fingerprint,
argv, extra environment, output collection, and progress. The runner creates
each job directory with mode 0700, runs one job at a time with the API
process environment plus the hook's variables, starts every command in a new
session, and stops the whole process group on cancel or timeout (SIGTERM, then
SIGKILL after 10 s); the next job starts only when no process of the previous
one is left. A cancelled job's outputs are final once its `completed_at` is set.
It serves only output and artifact files that resolve inside the job directory.
