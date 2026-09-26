"""ProcessJobRunner lifecycle with a tiny fake command: outputs, confinement, cancel, timeout, restart."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from annotation_studio import process_jobs
from annotation_studio.contracts import CapacityExceededError, IdempotencyConflictError
from annotation_studio.process_jobs import ProcessJobRunner, job_progress

FIXTURE = Path(__file__).parent / "fixtures" / "SAM_hbonds.cif"
TERMINAL = {"completed", "failed", "cancelled"}
# Writes a structure, two artifacts, and a symlink that leaves the job directory.
COMPLETE = """
import os, pathlib, shutil, sys
job = pathlib.Path(sys.argv[1])
shutil.copyfile(job / "input.cif", job / "structure.cif")
(job / "result.json").write_text('{"energy": -1.5}')
(job / "trajectory.cif").write_text("data_trajectory\\n")
(job / "outside.json").symlink_to(job.parent.parent / "secret.json")
print("[fake] cwd is job dir:", os.getcwd() == os.environ["FAKE_JOB_DIR"], flush=True)
"""
SLEEP = "import time; time.sleep(60)"
# Reports its parent's pid and its own only once it ignores SIGTERM, so only a group-wide
# SIGKILL stops it.
STUBBORN_GRANDCHILD = (
    "import os, pathlib, signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "(pathlib.Path(sys.argv[1]) / 'pids').write_text(f'{os.getppid()} {os.getpid()}'); time.sleep(60)"
)
# Marks that the job started; a {"stubborn": true} job then spawns a stubborn grandchild and waits.
STUBBORN_JOB = f"""
import json, pathlib, subprocess, sys, time
job = pathlib.Path(sys.argv[1])
(job / "started").touch()
if json.loads((job / "request.json").read_text())["stubborn"]:
    subprocess.Popen([sys.executable, "-c", {STUBBORN_GRANDCHILD!r}, sys.argv[1]])
    time.sleep(60)
"""


class FakeHooks:
    provider = "local-fake"
    id_prefix = "fake"

    def __init__(self, script: str) -> None:
        self.script = script

    def prepare(self, job_dir: Path, cif: str, request: dict[str, Any]) -> dict[str, Any]:
        del cif
        if request.get("invalid"):
            raise ValueError("invalid fake request")
        (job_dir / "request.json").write_text(json.dumps(request), encoding="utf-8")
        return request

    def fingerprint(self, cif: str, name: str, request: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps([cif, name, request], sort_keys=True).encode()).hexdigest()

    def command(self, job_dir: Path) -> list[str]:
        return [sys.executable, "-c", self.script, str(job_dir)]

    def env(self, job_dir: Path) -> dict[str, str]:
        return {"FAKE_JOB_DIR": str(job_dir)}

    def collect(self, job_dir: Path, return_code: int) -> tuple[list[dict[str, Any]], str | None]:
        if return_code != 0:
            return [], f"fake exited with status {return_code}"
        output = {
            "id": "structure",
            "label": "structure",
            "kind": "optimize",
            "rank": 1,
            "cif_path": str(job_dir / "structure.cif"),
            "metrics": {"energy": -1.5, "converged": True},
            "units": {"energy": "eV"},
            "artifacts": {
                "result": str(job_dir / "result.json"),
                "trajectory": str(job_dir / "trajectory.cif"),
                "outside": str(job_dir / "outside.json"),
                "missing": str(job_dir / "missing.npz"),
            },
        }
        escaped = {**output, "id": "escaped", "rank": 2, "cif_path": str(job_dir.parent.parent / "secret.json")}
        return [output, escaped], None

    def progress(self, record: dict[str, Any], log_path: Path | None, log_tail: str) -> dict[str, Any]:
        return job_progress(record, log_path, "Finished" if "[fake]" in log_tail else "Running")


@pytest.fixture
def make_runner(tmp_path: Path) -> Iterator[Callable[..., ProcessJobRunner]]:
    runners: list[ProcessJobRunner] = []

    def make(script: str, **options: Any) -> ProcessJobRunner:
        runner = ProcessJobRunner(FakeHooks(script), root=tmp_path / "jobs", **options)
        runners.append(runner)
        return runner

    yield make
    for runner in runners:
        runner.close()


def _submit(runner: ProcessJobRunner, request: dict[str, Any] | None = None, **options: Any) -> dict[str, Any]:
    return runner.submit(FIXTURE.read_text(encoding="utf-8"), "fixture", request or {}, **options)


def _wait(runner: ProcessJobRunner, job_id: str, statuses: set[str] = TERMINAL) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while (record := runner.get(job_id))["status"] not in statuses:
        assert time.monotonic() < deadline, record
        time.sleep(0.02)
    return record


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed grandchild stays a zombie until its new parent reaps it.
    stat = Path(f"/proc/{pid}/stat")
    return not (stat.is_file() and stat.read_text().rsplit(")", 1)[1].split()[0] == "Z")


def test_runner_collects_outputs_and_confines_artifacts(
    make_runner: Callable[..., ProcessJobRunner], tmp_path: Path
) -> None:
    (tmp_path / "secret.json").write_text('{"secret": true}', encoding="utf-8")
    runner = make_runner(COMPLETE)
    submitted = _submit(runner, {"steps": 1})
    assert submitted["id"].startswith("fake-")
    assert submitted["provider"] == "local-fake"
    assert submitted["request"] == {"steps": 1}
    job_dir = tmp_path / "jobs" / submitted["id"]
    assert job_dir.stat().st_mode & 0o777 == 0o700

    record = _wait(runner, submitted["id"])
    assert record["status"] == "completed", record["log_tail"]
    assert "[fake] cwd is job dir: True" in record["log_tail"]
    assert record["progress"]["phase"] == "Finished"
    assert record["metric_summary"]["energy"] == {"min": -1.5, "max": -1.5, "mean": -1.5}
    cif, output = runner.output_cif(record["id"], "structure")
    assert cif == FIXTURE.read_text(encoding="utf-8")
    assert output["units"] == {"energy": "eV"}
    assert runner.artifact(record["id"], "structure", "result") == (
        (job_dir / "result.json").resolve(),
        "application/json",
    )
    assert runner.artifact(record["id"], "structure", "trajectory")[1] == "chemical/x-mmcif"
    with pytest.raises(ValueError, match="escaped its workspace"):
        runner.artifact(record["id"], "structure", "outside")
    with pytest.raises(ValueError, match="escaped its workspace"):
        runner.output_cif(record["id"], "escaped")
    for output_id, name in (("structure", "missing"), ("structure", "unknown"), ("unknown", "result")):
        with pytest.raises(KeyError):
            runner.artifact(record["id"], output_id, name)
    with pytest.raises(KeyError):
        runner.get(record["id"], owner_id="someone-else")


def test_rejected_request_leaves_no_job_directory(make_runner: Callable[..., ProcessJobRunner], tmp_path: Path) -> None:
    runner = make_runner(COMPLETE)
    with pytest.raises(ValueError, match="invalid fake request"):
        _submit(runner, {"invalid": True})
    assert list((tmp_path / "jobs").iterdir()) == []


def test_runner_admission_and_idempotency(make_runner: Callable[..., ProcessJobRunner]) -> None:
    runner = make_runner(SLEEP)
    first = _submit(runner, {"steps": 1}, idempotency_key="key-1")
    assert _submit(runner, {"steps": 1}, idempotency_key="key-1")["id"] == first["id"]
    with pytest.raises(IdempotencyConflictError, match="different fake submission"):
        _submit(runner, {"steps": 2}, idempotency_key="key-1")
    with pytest.raises(CapacityExceededError, match="job queue is full"):
        _submit(runner, {"steps": 3})
    assert runner.cancel(first["id"])["status"] == "cancelled"


def test_cancel_stops_the_whole_process_group_before_the_next_job(
    make_runner: Callable[..., ProcessJobRunner], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(process_jobs, "STOP_GRACE_SECONDS", 0.5)
    runner = make_runner(STUBBORN_JOB, max_active_jobs=3)
    stubborn = _submit(runner, {"stubborn": True})
    following = _submit(runner, {"stubborn": False})
    dropped = _submit(runner, {"stubborn": False})
    cancelled_while_queued = runner.cancel(dropped["id"])
    assert cancelled_while_queued["status"] == "cancelled"
    assert cancelled_while_queued["completed_at"] is not None
    pids_path = tmp_path / "jobs" / stubborn["id"] / "pids"
    deadline = time.monotonic() + 20
    while not pids_path.is_file() or len(pids_path.read_text().split()) < 2:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    pids = [int(pid) for pid in pids_path.read_text().split()]

    cancelled = runner.cancel(stubborn["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["completed_at"] is None
    following_started = tmp_path / "jobs" / following["id"] / "started"
    deadline = time.monotonic() + 10
    while True:
        # Checked first: a process seen alive afterwards was alive when the next job started.
        started = following_started.exists()
        if not any(_alive(pid) for pid in pids):
            break
        assert not started, "the next job started while a process of the cancelled job was alive"
        assert time.monotonic() < deadline, "a process of the cancelled job survived"
        time.sleep(0.02)
    assert _wait(runner, following["id"])["status"] == "completed"
    record = runner.get(stubborn["id"])
    assert record["status"] == "cancelled"
    assert record["error"] is None
    assert record["completed_at"] is not None
    assert record["progress"]["indeterminate"] is False
    assert not (tmp_path / "jobs" / dropped["id"] / "started").exists()


def test_timeout_stops_the_job_and_fails_it(make_runner: Callable[..., ProcessJobRunner]) -> None:
    runner = make_runner(SLEEP, timeout_seconds=1)
    record = _wait(runner, _submit(runner)["id"])
    assert record["status"] == "failed"
    assert record["error"] == "the job exceeded its 1s timeout"
    assert record["outputs"] == []
    assert record["completed_at"] is not None


def test_restart_fails_interrupted_jobs_and_prunes_expired_ones(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    now = time.time()
    records = {
        "fake-1-running": {"status": "running", "updated_at": now, "completed_at": None},
        "fake-2-done": {"status": "completed", "updated_at": now - 60},
        "fake-3-expired": {"status": "failed", "updated_at": now - 30 * 24 * 60 * 60},
        "fake-4-mismatched": {"id": "fake-other", "status": "completed", "updated_at": now},
        "design-6-other-provider": {"provider": "local-rfd4", "status": "running", "updated_at": now},
    }
    for job_id, fields in records.items():
        (root / job_id).mkdir(parents=True)
        record = {"id": job_id, "provider": "local-fake", "owner_id": "local", "created_at": now, "error": None}
        (root / job_id / "job.json").write_text(json.dumps({**record, **fields}), encoding="utf-8")
    (root / "fake-5-corrupt").mkdir()
    (root / "fake-5-corrupt" / "job.json").write_text("{", encoding="utf-8")

    runner = ProcessJobRunner(FakeHooks(SLEEP), root=root)
    try:
        interrupted = runner.get("fake-1-running")
        assert interrupted["status"] == "failed"
        assert interrupted["error"] == "service restarted while the job was running"
        on_disk = json.loads((root / "fake-1-running" / "job.json").read_text(encoding="utf-8"))
        assert on_disk["status"] == "failed"
        assert on_disk["completed_at"] >= now
        done = runner.get("fake-2-done")
        assert done["status"] == "completed"
        assert done["completed_at"] == now - 60
        assert not (root / "fake-3-expired").exists()
        for job_id in ("fake-3-expired", "fake-4-mismatched", "fake-5-corrupt", "design-6-other-provider"):
            with pytest.raises(KeyError):
                runner.get(job_id)
        # Another provider's job in a shared root is neither adopted nor failed.
        other = json.loads((root / "design-6-other-provider" / "job.json").read_text(encoding="utf-8"))
        assert other["status"] == "running"
    finally:
        runner.close()
