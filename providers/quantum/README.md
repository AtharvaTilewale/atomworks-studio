# AtomWorks-Quantum provider for AtomWorks Studio

`atomworks-annotation-studio-quantum` lets Studio users run
[AtomWorks-Quantum](https://github.com/baker-laboratory/atomworks-quantum-dev) (aq)
calculations (single points, optimizations, frequencies, transition-state
searches, MD, protonation) on the structure they are editing, and open the
resulting structures in the editor.

It is a job-only provider: it registers the Studio job provider `quantum`
(label "AtomWorks-Quantum") and defines no document conditions, so another
provider (RFD4) or the neutral editor keeps the condition editor. Charge and
multiplicity are fields of the job request.

## How it works

The Studio API never imports atomworks-quantum. aq runs in its own Python
environment, one process per job, through its job-directory contract:

1. Studio writes `input.cif` (the session structure) and `request.json` into a
   private job directory (mode 0700).
2. It runs `<ANNOTATION_STUDIO_QUANTUM_COMMAND> job run <job dir>` in a new
   process session. aq prints one `[aq] stage i/n NAME STATUS` line per stage
   transition, which becomes the job's phase ("Stage 2/2: frequencies").
3. aq writes `outputs/manifest.json` last, also when a stage fails or the job
   is cancelled. Each finished stage becomes one job output: its
   `structure.cif` opens in the editor, its numeric results (energy, fmax,
   converged, n_imaginary, ...) are the output's metrics with their `units`, and
   its files (`result.json`, `arrays.npz`, trajectory CIFs, nested results such
   as `vibrations.result`) are downloadable artifacts.

A job that ends `unconverged` completes, with `converged: false` in the metrics
of the unconverged stage. Failed and cancelled jobs keep the outputs of the
stages that finished. Cancel and timeout stop the whole process group
(SIGTERM, then SIGKILL after 10 s); aq writes the manifest as cancelled on
SIGTERM.

## Install

Install aq in an environment of its own (it pins a different AtomWorks commit
than Studio), at a revision that has the `aq job` command:

```bash
git clone https://github.com/baker-laboratory/atomworks-quantum-dev.git /opt/atomworks-quantum
cd /opt/atomworks-quantum
uv sync --extra xtb --extra ts        # add the extras of the models you want to offer
.venv/bin/aq job schema > /dev/null   # fails if this aq has no job command
.venv/bin/aq models --installed       # the models Studio can offer
```

Then install this provider next to the Studio API: `uv sync --all-packages`
in the Studio workspace installs it, and so does the API's `quantum` extra.

The container build installs it with
`--build-arg ANNOTATION_STUDIO_INSTALL_QUANTUM_PROVIDER=true`, but the image
does not contain aq. A uv environment cannot be moved: its `python` links to
the interpreter it was created with, and its `aq` script names the
environment's own path. So either build a derived image that installs aq, or
mount the aq checkout and that interpreter at the paths they had when the
environment was created, readable by the image's user (uid 10001).
`UV_PYTHON_INSTALL_DIR=/opt/atomworks-quantum/python uv sync --managed-python ...`
keeps both under one directory. Then set `ANNOTATION_STUDIO_QUANTUM_COMMAND`
to the absolute `aq` path.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANNOTATION_STUDIO_QUANTUM_COMMAND` | unset (jobs disabled) | argv prefix of aq, split like a shell command, e.g. `/opt/atomworks-quantum/.venv/bin/aq`; use absolute paths, because aq runs with the job directory as its working directory |
| `ANNOTATION_STUDIO_QUANTUM_JOB_DIR` | `$ANNOTATION_STUDIO_JOB_DIR/quantum` | Job directories; jobs are disabled when neither is set |
| `ANNOTATION_STUDIO_QUANTUM_TIMEOUT_SECONDS` | `14400` (4 h) | Wall time of one job |
| `ANNOTATION_STUDIO_QUANTUM_MAX_ACTIVE_JOBS` | `1` | Queued plus running jobs; one job runs at a time |
| `ANNOTATION_STUDIO_QUANTUM_MODELS` | unset (all installed models) | Comma-separated catalog names offered and accepted, e.g. `gfn2-xtb,mace-off23-small` |
| `ANNOTATION_STUDIO_JOB_RETENTION_SECONDS` | `604800` (7 days) | Shared with RFD4: finished jobs older than this are deleted |

A setting the provider cannot use (an integer that does not parse, a command
that does not split or starts with a relative path) makes it unavailable (a
startup warning, or a startup error with `ANNOTATION_STUDIO_PROVIDER_REQUIRED=1`).

aq inherits the API process environment, as RFD4 does. Variables set for the
API therefore reach both aq and RFD4, and so does the `PYTHONPATH` that
Studio's launchers (`run.sh`, `deploy/*/start.sh`) set for the API. Give aq
its own settings in the command, which may start with `env` from `PATH`:

```bash
ANNOTATION_STUDIO_QUANTUM_COMMAND="env -u PYTHONPATH OMP_NUM_THREADS=8 CUDA_VISIBLE_DEVICES=1 /opt/atomworks-quantum/.venv/bin/aq"
```

`env -u PYTHONPATH` keeps the API's `PYTHONPATH` out of aq's interpreter,
which would otherwise search those directories before its own packages. aq
also reads `AQ_<PROGRAM>_COMMAND` (ORCA, Gaussian, ...), `HF_TOKEN`/`HF_HOME`,
`XDG_CACHE_HOME` and `FAIRCHEM_CACHE_DIR`.

The job schema (`GET /api/providers/quantum/jobs/schema`) runs
`aq models --json --installed` and `aq job schema` on first use and reuses the
answer for 10 minutes; if either fails, the schema says `enabled: false` with
the `reason`. It lists the aq `tasks`, the offered `models` (name, backend,
charge, spin, elements, devices, hessian, license), `defaults`, and aq's
`request_schema`. aq's catalog lists the options each model cannot run
without (`required_options`: a `method` and a `basis` for PySCF, Psi4 and
Gaussian, the `input` keywords for ORCA, a `head` for `mace-mh-1`); since a
Studio request carries no options, such a model is offered only when its card
sets them itself, and `aq job validate` refuses a request for it (HTTP 422).

Durable (PostgreSQL) deployments run only RFD4 jobs through the durable
worker; quantum jobs always run on the API host, so run a single API replica
when you enable them.

## Requests

`POST /api/sessions/{session_id}/providers/quantum/jobs` takes aq's job
request (`aq job schema`) without calculator `options`; the `schema` tag may
be left out:

```json
{
  "charge": 0,
  "multiplicity": 1,
  "calculator": "gfn2-xtb",
  "stages": [{"task": "optimize", "fmax": 0.02}, {"task": "frequencies"}]
}
```

`charge` is an integer or `"formal"`, the sum of formal charges. aq accepts
`"formal"` only for structures that carry its hydrogen annotations, and
structures exported from Studio do not, so `"formal"` needs a `protonate`
stage first; otherwise give the integer charge. A calculator is
`"<model>[@<device>]"` or `{"model", "device", "dtype"}`, at the top level or
on a stage.

The job schema's `request_schema` is aq's own, so it still lists calculator
`options`. Studio accepts a subset of it: it refuses `options`, allows at most
16 stages and 64 KiB, and applies the model allowlist. It then runs
`aq job validate -`; an invalid request is refused with HTTP 422 and aq's
message, and a broken aq environment with 503.

## Security

- Requests carry no paths: Studio and aq refuse every key besides `schema`,
  `charge`, `multiplicity`, `calculator` and `stages` (so `inputs`, `output`,
  `slurm` and `executor`), no stage field takes a path, and every job reads
  `input.cif` and writes inside its own directory.
- Calculator `options` are refused, because some backend options name files on
  the server (a Gaussian `chk`, an ORCA `%moinp` block). Use
  `ANNOTATION_STUDIO_QUANTUM_MODELS` to keep expensive or licensed programs
  (ORCA, Gaussian) out of reach of website users.
- Outputs and artifacts are served only when they resolve inside the job
  directory. Job responses and the model catalog carry no server paths of
  their own (the catalog omits `source` and `options`).
- The browser sees aq's output and command verbatim: `log_tail` (the last
  4000 characters of aq's output), the `error` of a job that wrote no manifest
  (which quotes the end of `job.log`), the schema's `reason` and HTTP 503
  details. These can show the paths of the aq installation and of the job
  directory, for example in Python warnings and tracebacks.

## Tests

`tests/test_quantum_provider.py` runs the provider and the API routes against
`tests/fake_aq.py`, a stand-in for the aq CLI. The route tests load the real
rfd4 entry point next to quantum, so put the RFD4 sources on `PYTHONPATH` as
`make test` does. With a real aq environment, the integration test runs an
optimization and frequencies of water with GFN2-xTB:

```bash
PYTHONPATH=external/RFD4-Proteina-dev/src \
ANNOTATION_STUDIO_TEST_AQ_COMMAND=/opt/atomworks-quantum/.venv/bin/aq \
  uv run python scripts/run_pytest.py -q tests/test_quantum_provider.py
```

## PyMOL

`atomworks_studio_quantum.pymol` opens aq results in the AtomWorks Studio
PyMOL plugin. Install the extra into PyMOL's environment (this brings the
Studio API package along, but aq itself is not needed there) and register the module path `atomworks_studio_quantum.pymol`
from **Main -> Extensions...**:

```bash
python -m pip install -e packages/api -e "providers/quantum[pymol]"
```

`aq_load_result <result directory> [, name]` then loads the directory's
`structure.cif` as a tracked, editable object, loads each trajectory that
`result.json` lists (`trajectory`, `images`, `forward`, `reverse`, `points`)
as a disabled multi-state object `<name>_<field>`, and prints the result
summary. **Summarize changes** shows the object's total charge and
multiplicity until its atoms or formal charges are edited. Given a job directory or a pipeline
output, the command lists the result directories inside it (for a job:
`run/input/<stage>`).
