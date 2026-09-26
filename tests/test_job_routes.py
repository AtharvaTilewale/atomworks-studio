"""Provider registry and generic job routes, with a fake-command job provider installed next to rfd4."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from collections.abc import Iterator
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from annotation_studio import main
from annotation_studio import providers as provider_loader
from annotation_studio.design_jobs import RFD4_LABEL
from annotation_studio.process_jobs import ProcessJobRunner, job_progress
from annotation_studio.providers import ENTRY_POINT_GROUP, ProviderRegistry, load_providers
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

FIXTURE = Path(__file__).parent / "fixtures" / "SAM_hbonds.cif"
FAKE_MODULE = "fake_studio_job_provider"
FAKE_COMMAND = """
import json, pathlib, shutil, sys, time
job = pathlib.Path(sys.argv[1])
time.sleep(json.loads((job / "request.json").read_text())["sleep"])
shutil.copyfile(job / "input.cif", job / "structure.cif")
(job / "result.json").write_text('{"energy": -1.5}')
(job / "trajectory.cif").write_text("data_trajectory\\n")
(job / "outside.json").symlink_to(job.parent.parent / "secret.json")
"""
client = TestClient(main.app)


class FakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sleep: float = Field(default=0, ge=0, le=30)


class FakeHooks:
    provider = "local-fake"
    id_prefix = "fake"

    def prepare(self, job_dir: Path, cif: str, request: FakeRequest) -> dict[str, Any]:
        del cif
        (job_dir / "request.json").write_text(request.model_dump_json(), encoding="utf-8")
        return request.model_dump()

    def fingerprint(self, cif: str, name: str, request: FakeRequest) -> str:
        return hashlib.sha256(json.dumps([cif, name, request.model_dump()]).encode()).hexdigest()

    def command(self, job_dir: Path) -> list[str]:
        return [sys.executable, "-c", FAKE_COMMAND, str(job_dir)]

    def env(self, job_dir: Path) -> dict[str, str]:
        del job_dir
        return {}

    def collect(self, job_dir: Path, return_code: int) -> tuple[list[dict[str, Any]], str | None]:
        if return_code != 0:
            return [], f"fake exited with status {return_code}"
        output = {
            "id": "01-optimize",
            "label": "optimize · fake",
            "kind": "optimize",
            "rank": 1,
            "cif_path": str(job_dir / "structure.cif"),
            "metrics": {"energy": -1.5, "converged": True, "structure_path": "hidden"},
            "units": {"energy": "eV"},
            "artifacts": {
                name: str(job_dir / file_name)
                for name, file_name in (
                    ("result", "result.json"),
                    ("trajectory", "trajectory.cif"),
                    ("outside", "outside.json"),
                    ("missing", "missing.npz"),
                )
            },
        }
        return [output], None

    def progress(self, record: dict[str, Any], log_path: Path | None, log_tail: str) -> dict[str, Any]:
        del log_tail
        return job_progress(record, log_path, record["status"].capitalize())


class FakeJobProvider(ProcessJobRunner):
    program_label = "fake"

    def __init__(self, root: Path) -> None:
        super().__init__(FakeHooks(), root=root, timeout_seconds=60)

    def schema(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "provider": "local-fake", "label": "Fake jobs", "defaults": {"sleep": 0}}

    def parse_request(self, payload: dict[str, Any]) -> FakeRequest:
        return FakeRequest.model_validate(payload)


def _fake_module(name: str, **exports: Any) -> ModuleType:
    module = ModuleType(name)
    for key, value in exports.items():
        setattr(module, key, value)
    return module


def _install(monkeypatch: pytest.MonkeyPatch, *modules: tuple[str, ModuleType]) -> None:
    """Expose the real rfd4 entry point plus fake provider modules to load_providers."""

    rfd4 = [entry_point for entry_point in entry_points(group=ENTRY_POINT_GROUP) if entry_point.name == "rfd4"]
    fakes = []
    for name, module in modules:
        monkeypatch.setitem(sys.modules, module.__name__, module)
        fakes.append(EntryPoint(name=name, value=module.__name__, group=ENTRY_POINT_GROUP))

    def installed(*, group: str) -> list[EntryPoint]:
        return [*rfd4, *fakes] if group == ENTRY_POINT_GROUP else []

    monkeypatch.setattr(provider_loader, "entry_points", installed)
    monkeypatch.delenv("ANNOTATION_STUDIO_CONDITION_PROVIDER", raising=False)
    monkeypatch.delenv("ANNOTATION_STUDIO_PROVIDER_REQUIRED", raising=False)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[ProviderRegistry]:
    module = _fake_module(
        FAKE_MODULE,
        PROVIDER_LABEL="Fake jobs",
        create_job_provider=lambda project_root: FakeJobProvider(project_root / "fake-jobs"),
    )
    _install(monkeypatch, ("fake", module))
    loaded = load_providers(tmp_path)
    loaded.add_job_provider("rfd4", main.design_jobs, label=RFD4_LABEL)
    monkeypatch.setattr(main, "providers", loaded)
    yield loaded
    loaded.job_providers["fake"].close()


def _session() -> str:
    response = client.post(
        "/api/sessions",
        files={"file": ("SAM_hbonds.cif", FIXTURE.read_bytes(), "chemical/x-mmcif")},
    )
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def _wait(job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while (job := client.get(f"/api/providers/fake/jobs/{job_id}").json())["status"] in {"queued", "running"}:
        assert time.monotonic() < deadline, job
        time.sleep(0.05)
    return job


def test_document_provider_is_the_first_with_conditions_unless_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    condition_provider = object()
    _install(
        monkeypatch,
        ("alpha", _fake_module("fake_alpha_provider", create_job_provider=lambda project_root: None)),
        ("beta", _fake_module("fake_beta_provider", CONDITION_PROVIDER=condition_provider)),
    )
    loaded = load_providers(Path.cwd())
    # "alpha" exports no conditions and "beta" sorts before "rfd4".
    assert loaded.document_provider == "beta"
    assert loaded.condition_provider is condition_provider
    assert loaded.condition_operations == {}
    assert loaded.installed == ["alpha", "beta", "rfd4"]
    assert loaded.job_providers == {}

    monkeypatch.setenv("ANNOTATION_STUDIO_CONDITION_PROVIDER", "rfd4")
    assert load_providers(Path.cwd()).document_provider == "rfd4"
    for requested, message in (
        ("alpha", "exports no CONDITION_PROVIDER"),
        ("missing", "no installed provider has that name"),
    ):
        monkeypatch.setenv("ANNOTATION_STUDIO_CONDITION_PROVIDER", requested)
        with pytest.raises(RuntimeError, match=message):
            load_providers(Path.cwd())


def test_provider_failures_are_tolerated_unless_required(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(project_root: Path) -> None:
        raise RuntimeError(f"no runtime under {project_root}")

    _install(monkeypatch, ("jobs", _fake_module("fake_failing_jobs_provider", create_job_provider=unavailable)))
    missing = EntryPoint(name="broken", value="fake_module_that_does_not_exist", group=ENTRY_POINT_GROUP)
    installed = provider_loader.entry_points(group=ENTRY_POINT_GROUP)
    monkeypatch.setattr(provider_loader, "entry_points", lambda *, group: [*installed, missing])

    loaded = load_providers(Path("/runtime"))
    assert loaded.installed == ["jobs", "rfd4"]
    assert loaded.job_providers == {}
    assert loaded.document_provider == "rfd4"

    monkeypatch.setenv("ANNOTATION_STUDIO_CONDITION_PROVIDER", "broken")
    with pytest.raises(RuntimeError, match="failed to load: No module named 'fake_module_that_does_not_exist'"):
        load_providers(Path("/runtime"))
    monkeypatch.delenv("ANNOTATION_STUDIO_CONDITION_PROVIDER")
    monkeypatch.setenv("ANNOTATION_STUDIO_PROVIDER_REQUIRED", "1")
    with pytest.raises(RuntimeError, match=r"broken: .*; jobs: could not create its job provider: no runtime"):
        load_providers(Path("/runtime"))


def test_job_provider_names_are_unique(registry: ProviderRegistry) -> None:
    with pytest.raises(RuntimeError, match="more than one Studio job provider is named 'fake'"):
        registry.add_job_provider("fake", main.design_jobs, label="Duplicate")


def test_providers_are_listed_with_their_capabilities(registry: ProviderRegistry) -> None:
    del registry
    for prefix in ("/api", "/api/v1"):
        response = client.get(f"{prefix}/providers")
        assert response.status_code == 200, response.text
        listed = {provider["name"]: provider for provider in response.json()}
        assert listed["fake"] == {
            "name": "fake",
            "label": "Fake jobs",
            "enabled": True,
            "conditions": False,
            "jobs": True,
        }
        assert listed["rfd4"] == {
            "name": "rfd4",
            "label": RFD4_LABEL,
            "enabled": main.design_jobs.enabled,
            "conditions": True,
            "jobs": True,
        }
    assert client.get("/api/providers/fake/jobs/schema").json()["label"] == "Fake jobs"
    assert client.get("/api/v1/providers/rfd4/jobs/schema").json() == client.get("/api/v1/design/schema").json()
    missing = client.get("/api/providers/missing/jobs/schema")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "job provider not found: missing"
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/providers/{name}/jobs/{job_id}/outputs/{output_id}/artifacts/{artifact}" in paths


def test_submission_errors_map_to_http_statuses(registry: ProviderRegistry) -> None:
    del registry
    session_id = _session()
    jobs = f"/api/sessions/{session_id}/providers/fake/jobs"
    assert client.post(jobs, json={"unknown": 1}).status_code == 422
    assert client.post(jobs, json=[1]).status_code == 422
    assert client.post(f"/api/sessions/{session_id}/providers/missing/jobs", json={}).status_code == 404
    # The session and the Idempotency-Key are checked before the provider parses the body.
    assert client.post("/api/sessions/missing-session/providers/fake/jobs", json={"unknown": 1}).status_code == 404
    assert client.post(jobs, json={"unknown": 1}, headers={"Idempotency-Key": "not valid"}).status_code == 400

    running = client.post(jobs, json={"sleep": 30}, headers={"Idempotency-Key": "fake-1"})
    assert running.status_code == 200, running.text
    replay = client.post(jobs, json={"sleep": 30}, headers={"Idempotency-Key": "fake-1"})
    assert replay.json()["id"] == running.json()["id"]
    conflict = client.post(jobs, json={"sleep": 20}, headers={"Idempotency-Key": "fake-1"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "Idempotency-Key was already used for a different fake submission"
    assert client.post(jobs, json={}).status_code == 429
    cancelled = client.post(f"/api/v1/providers/fake/jobs/{running.json()['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert _wait(running.json()["id"])["status"] == "cancelled"
    assert client.post("/api/providers/fake/jobs/fake-0-missing/cancel").status_code == 404

    if not main.design_jobs.enabled:
        disabled = client.post(f"/api/sessions/{session_id}/providers/rfd4/jobs", json={"num_designs": 1})
        assert disabled.status_code == 503
        assert disabled.json()["detail"] == "design jobs are not configured on this deployment"


def test_completed_job_outputs_open_align_and_download(registry: ProviderRegistry, tmp_path: Path) -> None:
    del registry
    (tmp_path / "secret.json").write_text('{"secret": true}', encoding="utf-8")
    session_id = _session()
    submitted = client.post(f"/api/sessions/{session_id}/providers/fake/jobs", json={})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["input"]["session_id"] == session_id
    job = _wait(submitted.json()["id"])
    assert job["status"] == "completed", job["log_tail"]
    assert job["request"] == {"sleep": 0.0}
    assert set(job) >= {"id", "provider", "status", "progress", "log_tail", "metric_summary", "input", "outputs"}
    assert not set(job) & {"owner_id", "idempotency_key", "request_fingerprint", "source_session_id"}
    assert job["outputs"] == [
        {
            "id": "01-optimize",
            "label": "optimize · fake",
            "kind": "optimize",
            "rank": 1,
            "metrics": {"energy": -1.5, "converged": True},
            "units": {"energy": "eV"},
            "artifacts": [
                {"name": "result", "label": "result.json"},
                {"name": "trajectory", "label": "trajectory.cif"},
                {"name": "outside", "label": "outside.json"},
                {"name": "missing", "label": "missing.npz"},
            ],
        }
    ]
    assert client.get(f"/api/providers/fake/jobs/{job['id']}").text.count(str(tmp_path)) == 0

    output = f"/api/providers/fake/jobs/{job['id']}/outputs/01-optimize"
    opened = client.post(f"{output}/open")
    assert opened.status_code == 200, opened.text
    assert opened.json()["structure"]["atom_count"] > 0
    assert opened.json()["artifacts"] == job["outputs"][0]["artifacts"]
    assert client.post(f"{output}/open").json()["session_id"] == opened.json()["session_id"]
    aligned = client.post(f"/api/v1{output.removeprefix('/api')}/align-to-input")
    assert aligned.status_code == 200, aligned.text
    assert aligned.json()["session_id"] == opened.json()["session_id"]
    assert aligned.json()["alignment"]
    assert client.post(f"/api/providers/fake/jobs/{job['id']}/outputs/missing/open").status_code == 404

    download = client.get(f"{output}/artifacts/result")
    assert download.status_code == 200
    assert download.json() == {"energy": -1.5}
    assert download.headers["content-type"] == "application/json"
    assert 'filename="result.json"' in download.headers["content-disposition"]
    assert download.headers["cache-control"] == "no-store"
    assert client.get(f"{output}/artifacts/trajectory").headers["content-type"].startswith("chemical/x-mmcif")
    escaped = client.get(f"{output}/artifacts/outside")
    assert escaped.status_code == 422
    assert "secret" not in escaped.text
    for path in (
        f"{output}/artifacts/missing",
        f"{output}/artifacts/unknown",
        f"/api/providers/fake/jobs/{job['id']}/outputs/unknown/artifacts/result",
        "/api/providers/fake/jobs/fake-0-missing/outputs/01-optimize/artifacts/result",
        f"/api/providers/missing/jobs/{job['id']}/outputs/01-optimize/artifacts/result",
    ):
        assert client.get(path).status_code == 404, path
    assert client.get("/api/providers/fake/jobs/fake-0-missing").json()["detail"] == "job not found"
    assert client.get("/api/design-jobs/fake-0-missing").json()["detail"] == "design job not found"
