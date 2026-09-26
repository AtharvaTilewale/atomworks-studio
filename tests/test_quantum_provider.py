"""The quantum job provider against a fake aq CLI, and one real aq job when ANNOTATION_STUDIO_TEST_AQ_COMMAND is set."""

from __future__ import annotations

import json
import os
import shlex
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from annotation_studio import main, process_jobs
from annotation_studio.contracts import CapacityExceededError, IdempotencyConflictError
from annotation_studio.design_jobs import RFD4_LABEL
from annotation_studio.providers import ProviderRegistry, load_providers
from atomworks_studio_quantum import jobs
from atomworks_studio_quantum.jobs import QuantumJobHooks, QuantumJobProvider
from fastapi.testclient import TestClient

FAKE_AQ = [sys.executable, str(Path(__file__).parent / "fake_aq.py")]
REAL_AQ = os.environ.get("ANNOTATION_STUDIO_TEST_AQ_COMMAND")
TERMINAL = {"completed", "failed", "cancelled"}
WATER = """data_water
#
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.pdbx_formal_charge
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.pdbx_PDB_model_num
HETATM 1 O O  . HOH A 1 . ? 0.000 0.000 0.117 1.00 0.00 0 1 HOH A O  1
HETATM 2 H H1 . HOH A 1 . ? 0.000 0.757 -0.469 1.00 0.00 0 1 HOH A H1 1
HETATM 3 H H2 . HOH A 1 . ? 0.000 -0.757 -0.469 1.00 0.00 0 1 HOH A H2 1
#
"""
REQUEST: dict[str, Any] = {
    "charge": 0,
    "multiplicity": 1,
    "calculator": "gfn2-xtb",
    "stages": [{"task": "optimize", "fmax": 0.02}, {"task": "frequencies"}],
}
client = TestClient(main.app)


@pytest.fixture
def make_provider(tmp_path: Path) -> Iterator[Callable[..., QuantumJobProvider]]:
    providers: list[QuantumJobProvider] = []

    def make(**options: Any) -> QuantumJobProvider:
        options.setdefault("root", tmp_path / "jobs")
        options.setdefault("command", FAKE_AQ)
        provider = QuantumJobProvider(**options)
        providers.append(provider)
        return provider

    yield make
    for provider in providers:
        provider.close()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[[], list[str]]:
    """Record every fake aq invocation; return a reader of the recorded argv lines."""

    path = tmp_path / "aq-calls.txt"
    monkeypatch.setenv("FAKE_AQ_CALLS", str(path))
    return lambda: path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def _submit(provider: QuantumJobProvider, payload: dict[str, Any] = REQUEST, **options: Any) -> dict[str, Any]:
    return provider.submit(WATER, "water", provider.parse_request(payload), **options)


def _wait(provider: QuantumJobProvider, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while (record := provider.get(job_id))["status"] not in TERMINAL:
        assert time.monotonic() < deadline, record
        time.sleep(0.02)
    return record


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return not (stat.is_file() and stat.read_text().rsplit(")", 1)[1].split()[0] == "Z")


def test_completed_job_maps_the_manifest_to_outputs(
    make_provider: Callable[..., QuantumJobProvider], tmp_path: Path
) -> None:
    provider = make_provider()
    submitted = _submit(provider)
    assert submitted["id"].startswith("quantum-")
    assert submitted["provider"] == "quantum"
    assert submitted["request"] == {"schema": "atomworks-quantum.job/1", **REQUEST}
    job_dir = tmp_path / "jobs" / submitted["id"]
    assert json.loads((job_dir / "request.json").read_text(encoding="utf-8")) == submitted["request"]

    record = _wait(provider, submitted["id"])
    assert record["status"] == "completed", record["log_tail"]
    assert record["error"] is None
    assert "[aq] stage 2/2 01-frequencies done" in record["log_tail"]
    assert record["progress"]["phase"] == "Completed"
    optimize, frequencies = record["outputs"]
    assert optimize == {
        "id": "00-optimize",
        "label": "optimize · gfn2-xtb",
        "kind": "optimize",
        "rank": 1,
        "cif_path": str(job_dir / "run/input/00-optimize/structure.cif"),
        "metrics": {"energy": -137.9, "fmax": 0.01, "converged": True, "steps": 12, "charge": 0, "multiplicity": 1},
        "units": {"energy": "eV", "fmax": "eV/Å"},
        "artifacts": {
            "result": str(job_dir / "run/input/00-optimize/result.json"),
            "trajectory": str(job_dir / "run/input/00-optimize/trajectory.cif"),
        },
    }
    assert (frequencies["id"], frequencies["rank"], frequencies["metrics"]["n_imaginary"]) == ("01-frequencies", 2, 0)
    assert record["metric_summary"]["energy"]["mean"] == -137.9
    assert provider.output_cif(record["id"], "01-frequencies")[0] == WATER
    assert provider.artifact(record["id"], "00-optimize", "result") == (
        (job_dir / "run/input/00-optimize/result.json").resolve(),
        "application/json",
    )
    assert provider.artifact(record["id"], "00-optimize", "trajectory")[1] == "chemical/x-mmcif"
    with pytest.raises(KeyError):
        provider.artifact(record["id"], "01-frequencies", "trajectory")


def test_progress_names_the_running_stage() -> None:
    hooks = QuantumJobHooks(FAKE_AQ)
    now = time.time()
    running = {"status": "running", "created_at": now, "updated_at": now}
    tail = "[aq] job started: 2 stages, calculator gfn2-xtb\n[aq] stage 1/2 00-optimize done\n"
    assert hooks.progress(running, None, "")["phase"] == "Starting aq"
    assert hooks.progress(running, None, tail)["phase"] == "Stage 1/2: optimize"
    progress = hooks.progress(running, None, tail + "[aq] stage 2/2 01-my-freq running\nstep 3\n")
    assert progress["phase"] == "Stage 2/2: my-freq"
    assert progress["indeterminate"] is True
    assert hooks.progress({**running, "status": "queued"}, None, "")["phase"] == "Queued"


@pytest.mark.parametrize(
    ("mode", "status", "error", "output_ids"),
    [
        ("failed", "failed", "CalculatorUnavailableError: the ts extra is not installed.", ["00-optimize"]),
        ("failed-without-error", "failed", "aq job failed with exit status 1", ["00-optimize", "01-frequencies"]),
        ("crash", "failed", "aq exited with status 139 without writing outputs/manifest.json; last output:", []),
        ("invalid", "failed", "aq wrote an unreadable outputs/manifest.json: 1 validation error for Manifest", []),
        ("unconverged", "completed", None, ["00-optimize", "01-frequencies"]),
    ],
)
def test_job_outcomes_follow_the_manifest(
    make_provider: Callable[..., QuantumJobProvider],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    status: str,
    error: str | None,
    output_ids: list[str],
) -> None:
    monkeypatch.setenv("FAKE_AQ_RUN", mode)
    provider = make_provider()
    record = _wait(provider, _submit(provider)["id"])
    assert record["status"] == status, record["log_tail"]
    assert [output["id"] for output in record["outputs"]] == output_ids
    if error is None:
        assert record["error"] is None
        assert record["outputs"][-1]["metrics"]["converged"] is False
    else:
        assert record["error"].startswith(error)
    if mode == "crash":
        assert record["error"].endswith("Fatal Python error: Segmentation fault")


def test_cancel_stops_the_aq_process_group_and_keeps_finished_stages(
    make_provider: Callable[..., QuantumJobProvider], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(process_jobs, "STOP_GRACE_SECONDS", 0.5)
    monkeypatch.setenv("FAKE_AQ_RUN", "slow")
    provider = make_provider()
    submitted = _submit(provider)
    pids_path = tmp_path / "jobs" / submitted["id"] / "pids"
    deadline = time.monotonic() + 20
    while not pids_path.is_file() or not pids_path.read_text():
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert provider.get(submitted["id"])["progress"]["phase"] == "Stage 2/2: frequencies"
    pids = [int(pid) for pid in pids_path.read_text().split()]

    assert provider.cancel(submitted["id"])["status"] == "cancelled"
    deadline = time.monotonic() + 10
    while any(_alive(pid) for pid in pids):
        assert time.monotonic() < deadline, "a process of the cancelled aq job survived"
        time.sleep(0.05)
    deadline = time.monotonic() + 10
    while not (record := provider.get(submitted["id"]))["outputs"]:
        assert time.monotonic() < deadline, record
        time.sleep(0.02)
    assert (record["status"], record["error"]) == ("cancelled", None)
    assert [output["id"] for output in record["outputs"]] == ["00-optimize"]
    assert "[aq] job cancelled" in record["log_tail"]


def test_admission_and_idempotency(
    make_provider: Callable[..., QuantumJobProvider], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_AQ_RUN", "slow")
    provider = make_provider()
    first = _submit(provider, idempotency_key="key-1")
    assert _submit(provider, idempotency_key="key-1")["id"] == first["id"]
    with pytest.raises(IdempotencyConflictError, match="different quantum submission"):
        _submit(provider, {**REQUEST, "charge": 1}, idempotency_key="key-1")
    with pytest.raises(CapacityExceededError, match="quantum job queue is full"):
        _submit(provider, {**REQUEST, "charge": -1})
    assert provider.cancel(first["id"])["status"] == "cancelled"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"output": "/tmp/elsewhere"}, "Extra inputs are not permitted"),
        ({"inputs": ["/etc/passwd"]}, "Extra inputs are not permitted"),
        ({"slurm": {"partition": "cpu"}}, "Extra inputs are not permitted"),
        ({"schema": "atomworks-quantum.job/2"}, "atomworks-quantum.job/1"),
        ({"multiplicity": 0}, "greater than or equal to 1"),
        ({"stages": []}, "at least 1 item"),
        ({"stages": [{"task": "singlepoint"}] * 17}, "at most 16 items"),
        ({"stages": [{"task": "md", "note": "x" * 70_000}]}, "exceeds 64 KiB"),
        ({"calculator": {"model": "gaussian", "options": {"chk": "/home/x.chk"}}}, "calculator options are not"),
        ({"stages": [{"task": "singlepoint", "calculator": {"model": "orca", "options": {}}}]}, "calculator options"),
    ],
)
def test_requests_are_refused_before_aq_runs(
    make_provider: Callable[..., QuantumJobProvider],
    calls: Callable[[], list[str]],
    change: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        make_provider().parse_request({**REQUEST, **change})
    assert calls() == []


def test_aq_validates_the_request_it_will_run(
    make_provider: Callable[..., QuantumJobProvider], calls: Callable[[], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = make_provider()
    with pytest.raises(ValueError, match="unknown model 'nosuch'"):
        provider.parse_request({**REQUEST, "calculator": "nosuch@cpu"})
    with pytest.raises(ValueError, match="unknown task 'dock'"):
        provider.parse_request({**REQUEST, "stages": [{"task": "dock"}]})
    # A model that is not offered because it needs options: aq refuses it before a job starts.
    with pytest.raises(ValueError, match=r"mace-mh-1 needs the option\(s\) 'head', for example"):
        provider.parse_request({**REQUEST, "stages": [{"task": "singlepoint", "calculator": "mace-mh-1"}]})
    assert calls() == ["job validate -"] * 3
    monkeypatch.setenv("FAKE_AQ_BROKEN", "1")
    with pytest.raises(RuntimeError, match="aq job validate failed with exit status 1: ImportError: cannot import"):
        provider.parse_request(REQUEST)


def test_model_allowlist_limits_offered_and_accepted_models(make_provider: Callable[..., QuantumJobProvider]) -> None:
    provider = make_provider(models=frozenset({"gfn2-xtb", "not-installed"}))
    assert [model["name"] for model in provider.schema()["models"]] == ["gfn2-xtb"]
    provider.parse_request({**REQUEST, "calculator": {"model": "gfn2-xtb", "device": "cpu"}})
    stage_model = {**REQUEST, "stages": [{"task": "singlepoint", "calculator": "mace-off23-small@cuda"}]}
    refusal = "mace-off23-small are not offered on this deployment; allowed models: gfn2-xtb, not-installed"
    with pytest.raises(ValueError, match=refusal):
        provider.parse_request(stage_model)
    nothing_installed = make_provider(models=frozenset({"not-installed"})).schema()
    assert nothing_installed["enabled"] is False
    assert nothing_installed["reason"] == "aq has no installed model that ANNOTATION_STUDIO_QUANTUM_MODELS allows"


def test_schema_is_cached_and_says_why_jobs_are_disabled(
    make_provider: Callable[..., QuantumJobProvider],
    calls: Callable[[], list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = make_provider()
    schema = provider.schema()
    assert provider.schema() == schema
    assert calls() == ["models --json --installed", "job schema"]
    assert {key: schema[key] for key in ("enabled", "provider", "label", "tasks")} == {
        "enabled": True,
        "provider": "quantum",
        "label": "AtomWorks-Quantum",
        "tasks": ["frequencies", "md", "optimize", "singlepoint"],
    }
    assert schema["models"][1] == {
        "name": "mace-off23-small",
        "backend": "mace",
        "charge": None,
        "spin": None,
        "elements": None,
        "devices": ["cpu", "cuda"],
        "hessian": None,
        "license": None,
    }
    # aq's catalog says that pyscf requires a method and a basis and mace-mh-1 a head (required_options), which a
    # Studio request cannot set; orca's card sets its input itself, and mace-off23-small, printed without
    # required_options, requires nothing.
    assert [model["name"] for model in schema["models"]] == ["gfn2-xtb", "mace-off23-small", "orca"]
    assert schema["defaults"]["calculator"] == "gfn2-xtb"
    provider.parse_request(schema["defaults"])
    assert schema["request_schema"]["title"] == "JobRequest"
    monkeypatch.setattr(jobs, "CATALOG_TTL_SECONDS", -1)
    monkeypatch.setenv("FAKE_AQ_BROKEN", "1")
    broken = provider.schema()
    assert broken == {
        "enabled": False,
        "provider": "quantum",
        "label": "AtomWorks-Quantum",
        "reason": "aq models --json --installed failed with exit status 1: ImportError: cannot import name 'tblite'",
    }
    assert provider.enabled

    missing = make_provider(command=["/nonexistent/aq"]).schema()["reason"]
    assert missing.startswith("could not run /nonexistent/aq models --json --installed: [Errno 2]")
    unconfigured = make_provider(command=None)
    assert not unconfigured.enabled
    assert unconfigured.schema()["reason"] == "ANNOTATION_STUDIO_QUANTUM_COMMAND is not set"
    with pytest.raises(RuntimeError, match="quantum jobs are not configured on this deployment"):
        unconfigured.parse_request(REQUEST)
    no_directory = make_provider(root=None)
    assert no_directory.schema()["reason"].startswith("neither ANNOTATION_STUDIO_QUANTUM_JOB_DIR nor")


def test_outputs_outside_the_job_directory_are_not_served(
    make_provider: Callable[..., QuantumJobProvider], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "secret.cif").write_text("data_secret\n", encoding="utf-8")
    (tmp_path / "secret.json").write_text('{"secret": true}', encoding="utf-8")
    monkeypatch.setenv("FAKE_AQ_RUN", "escape")
    provider = make_provider()
    record = _wait(provider, _submit(provider)["id"])
    assert record["status"] == "completed", record["log_tail"]
    with pytest.raises(ValueError, match="escaped its workspace"):
        provider.output_cif(record["id"], "00-optimize")
    with pytest.raises(ValueError, match="escaped its workspace"):
        provider.artifact(record["id"], "00-optimize", "outside")
    assert provider.artifact(record["id"], "00-optimize", "result")[1] == "application/json"


def test_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("QUANTUM_JOB_DIR", "QUANTUM_MODELS", "QUANTUM_TIMEOUT_SECONDS", "JOB_RETENTION_SECONDS"):
        monkeypatch.delenv(f"ANNOTATION_STUDIO_{name}", raising=False)
    monkeypatch.setenv("ANNOTATION_STUDIO_JOB_DIR", str(tmp_path / "studio-jobs"))
    monkeypatch.setenv("ANNOTATION_STUDIO_QUANTUM_COMMAND", "/opt/aq/bin/aq --debug")
    monkeypatch.setenv("ANNOTATION_STUDIO_QUANTUM_MAX_ACTIVE_JOBS", "3")
    provider = QuantumJobProvider.from_environment()
    assert (provider.root, provider.command, provider.models) == (
        tmp_path / "studio-jobs" / "quantum",
        ["/opt/aq/bin/aq", "--debug"],
        None,
    )
    assert (provider.timeout_seconds, provider.max_active_jobs) == (4 * 60 * 60, 3)
    assert provider.hooks.command(Path("/jobs/quantum-1")) == [
        "/opt/aq/bin/aq",
        "--debug",
        "job",
        "run",
        "/jobs/quantum-1",
    ]
    provider.close()

    monkeypatch.setenv("ANNOTATION_STUDIO_QUANTUM_JOB_DIR", str(tmp_path / "quantum-jobs"))
    monkeypatch.setenv("ANNOTATION_STUDIO_QUANTUM_MODELS", " gfn2-xtb, mace-off23-small ,")
    provider = QuantumJobProvider.from_environment()
    assert (provider.root, provider.models) == (tmp_path / "quantum-jobs", {"gfn2-xtb", "mace-off23-small"})
    provider.close()
    monkeypatch.setenv("ANNOTATION_STUDIO_QUANTUM_TIMEOUT_SECONDS", "4h")
    with pytest.raises(RuntimeError, match="ANNOTATION_STUDIO_QUANTUM_TIMEOUT_SECONDS must be an integer; got '4h'"):
        QuantumJobProvider.from_environment()


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ('/opt/aq/bin/aq "--debug', "ANNOTATION_STUDIO_QUANTUM_COMMAND is not a valid command line: No closing"),
        (".venv/bin/aq --debug", "must start with an absolute path or a program on PATH, .* got '.venv/bin/aq'"),
    ],
)
def test_unusable_commands_leave_the_api_running_without_quantum_jobs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str, message: str
) -> None:
    monkeypatch.setenv("ANNOTATION_STUDIO_QUANTUM_COMMAND", command)
    with pytest.raises(RuntimeError, match=message):
        QuantumJobProvider.from_environment()
    for name in ("CONDITION_PROVIDER", "PROVIDER_REQUIRED"):
        monkeypatch.delenv(f"ANNOTATION_STUDIO_{name}", raising=False)
    registry = load_providers(tmp_path)
    assert "quantum" in registry.installed
    assert "quantum" not in registry.job_providers


def _load_quantum_next_to_rfd4(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str) -> ProviderRegistry:
    """Load the installed provider entry points with quantum jobs configured, as the API does."""

    monkeypatch.setenv("ANNOTATION_STUDIO_QUANTUM_COMMAND", command)
    monkeypatch.setenv("ANNOTATION_STUDIO_QUANTUM_JOB_DIR", str(tmp_path / "jobs"))
    for name in ("QUANTUM_MODELS", "CONDITION_PROVIDER", "PROVIDER_REQUIRED"):
        monkeypatch.delenv(f"ANNOTATION_STUDIO_{name}", raising=False)
    registry = load_providers(tmp_path)
    assert "quantum" in registry.installed, "install the workspace: uv sync --all-packages"
    assert "rfd4" in registry.installed, "put external/RFD4-Proteina-dev/src on PYTHONPATH, as make test does"
    registry.add_job_provider("rfd4", main.design_jobs, label=RFD4_LABEL)
    monkeypatch.setattr(main, "providers", registry)
    return registry


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[ProviderRegistry]:
    registry = _load_quantum_next_to_rfd4(monkeypatch, tmp_path, shlex.join(FAKE_AQ))
    yield registry
    registry.job_providers["quantum"].close()


def _session() -> str:
    response = client.post("/api/sessions", files={"file": ("water.cif", WATER.encode(), "chemical/x-mmcif")})
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def _wait_route(job_id: str, timeout: float = 20) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while (job := client.get(f"/api/providers/quantum/jobs/{job_id}").json())["status"] not in TERMINAL:
        assert time.monotonic() < deadline, job
        time.sleep(0.05)
    return job


def test_api_routes_run_quantum_jobs_next_to_rfd4(fake_registry: ProviderRegistry, tmp_path: Path) -> None:
    del fake_registry
    listed = {provider["name"]: provider for provider in client.get("/api/v1/providers").json()}
    assert listed["quantum"] == {
        "name": "quantum",
        "label": "AtomWorks-Quantum",
        "enabled": True,
        "conditions": False,
        "jobs": True,
    }
    # quantum sorts first but exports no conditions, so rfd4 keeps the condition editor.
    assert (listed["rfd4"]["conditions"], listed["rfd4"]["jobs"]) == (True, True)
    assert client.get("/api/providers/quantum/jobs/schema").json()["enabled"] is True

    session_id = _session()
    jobs_route = f"/api/sessions/{session_id}/providers/quantum/jobs"
    for payload, detail in (
        ({**REQUEST, "output": "/tmp"}, "Extra inputs are not permitted"),
        ({**REQUEST, "calculator": {"model": "orca", "options": {"blocks": "%moinp"}}}, "calculator options"),
        ({**REQUEST, "calculator": "nosuch"}, "unknown model 'nosuch'"),
    ):
        refused = client.post(jobs_route, json=payload)
        assert refused.status_code == 422, refused.text
        assert detail in refused.json()["detail"]

    submitted = client.post(jobs_route, json=REQUEST)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["input"]["session_id"] == session_id
    job = _wait_route(submitted.json()["id"])
    assert job["status"] == "completed", job["log_tail"]
    assert job["outputs"][0] == {
        "id": "00-optimize",
        "label": "optimize · gfn2-xtb",
        "kind": "optimize",
        "rank": 1,
        "metrics": {"energy": -137.9, "fmax": 0.01, "converged": True, "steps": 12, "charge": 0, "multiplicity": 1},
        "units": {"energy": "eV", "fmax": "eV/Å"},
        "artifacts": [{"name": "result", "label": "result.json"}, {"name": "trajectory", "label": "trajectory.cif"}],
    }
    assert str(tmp_path) not in client.get(f"/api/v1/providers/quantum/jobs/{job['id']}").text

    output = f"/api/providers/quantum/jobs/{job['id']}/outputs/01-frequencies"
    opened = client.post(f"{output}/open")
    assert opened.status_code == 200, opened.text
    assert opened.json()["structure"]["atom_count"] == 3
    aligned = client.post(f"{output}/align-to-input")
    assert aligned.status_code == 200, aligned.text
    assert aligned.json()["session_id"] == opened.json()["session_id"]
    download = client.get(f"{output}/artifacts/result")
    assert download.status_code == 200
    assert download.json()["n_imaginary"] == 0
    assert download.headers["content-type"] == "application/json"
    assert client.get(f"{output}/artifacts/trajectory").status_code == 404
    assert client.post(f"/api/providers/quantum/jobs/{job['id']}/cancel").json()["status"] == "completed"


@pytest.mark.skipif(not REAL_AQ, reason="set ANNOTATION_STUDIO_TEST_AQ_COMMAND to an aq executable to run a real job")
def test_real_aq_optimizes_water_and_computes_its_frequencies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert REAL_AQ is not None
    registry = _load_quantum_next_to_rfd4(monkeypatch, tmp_path, REAL_AQ)
    try:
        schema = client.get("/api/providers/quantum/jobs/schema").json()
        assert schema["enabled"] is True, schema
        offered = {model["name"] for model in schema["models"]}
        assert "gfn2-xtb" in offered
        # The catalog cards of these models require options (required_options) that they do not set themselves.
        assert not offered & {"orca", "gaussian", "pyscf", "gpu4pyscf", "psi4", "psi4-cuest", "mace-mh-1"}
        assert {"optimize", "frequencies", "find_ts", "md"} <= set(schema["tasks"])

        session_id = _session()
        jobs_route = f"/api/sessions/{session_id}/providers/quantum/jobs"
        refused = client.post(jobs_route, json={**REQUEST, "calculator": "nosuch"})
        assert refused.status_code == 422
        assert "unknown model 'nosuch'" in refused.json()["detail"]
        refused = client.post(jobs_route, json={**REQUEST, "calculator": "pyscf"})
        assert refused.status_code == 422
        assert "pyscf needs the option(s) 'method', 'basis'" in refused.json()["detail"]

        request = {**REQUEST, "stages": [{"task": "optimize", "fmax": 0.005}, {"task": "frequencies"}]}
        submitted = client.post(jobs_route, json=request)
        assert submitted.status_code == 200, submitted.text
        job = _wait_route(submitted.json()["id"], timeout=600)
        assert job["status"] == "completed", (job["error"], job["log_tail"])
        assert "[aq] stage 2/2 01-frequencies done" in job["log_tail"]
        optimize, frequencies = job["outputs"]
        assert (optimize["id"], optimize["label"]) == ("00-optimize", "optimize · gfn2-xtb · H2O")
        assert optimize["metrics"]["converged"] is True
        assert optimize["metrics"]["fmax"] < 0.005
        assert optimize["units"]["energy"] == "eV"
        assert {"result", "arrays", "trajectory"} <= {artifact["name"] for artifact in optimize["artifacts"]}
        assert frequencies["metrics"]["n_imaginary"] == 0

        output = f"/api/providers/quantum/jobs/{job['id']}/outputs/{frequencies['id']}"
        opened = client.post(f"{output}/open")
        assert opened.status_code == 200, opened.text
        assert opened.json()["structure"]["atom_count"] == 3
        download = client.get(f"{output}/artifacts/result")
        assert download.status_code == 200
        assert isinstance(download.json(), dict)
    finally:
        registry.job_providers["quantum"].close()
