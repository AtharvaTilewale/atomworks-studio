"""Provider-neutral local jobs that run one argv-only subprocess per job."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from .contracts import CapacityExceededError, IdempotencyConflictError

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
# Seconds a cancelled or timed-out process group gets between SIGTERM and SIGKILL.
STOP_GRACE_SECONDS = 10.0
ARTIFACT_MEDIA_TYPES = {
    ".cif": "chemical/x-mmcif",
    ".mmcif": "chemical/x-mmcif",
    ".json": "application/json",
}


class ProcessJobHooks(Protocol):
    """Provider-specific half of a ProcessJobRunner.

    id_prefix starts every job id and names the jobs in error messages ("design jobs are
    not configured"). The runner writes job_dir/input.cif before prepare, which writes the
    provider's files and returns the public request record. The command runs with the API
    process environment plus env(job_dir); its combined output goes to job_dir/job.log.
    collect returns the outputs and an error message (None on success); it is called for
    every job that started, including cancelled and timed-out ones. Outputs carry id,
    label, kind, rank, metrics, an absolute cif_path, and optionally units and artifacts
    ({name: absolute path}); every path must stay inside job_dir.
    """

    provider: str
    id_prefix: str

    def prepare(self, job_dir: Path, cif: str, request: Any) -> dict[str, Any]: ...

    def fingerprint(self, cif: str, name: str, request: Any) -> str: ...

    def command(self, job_dir: Path) -> list[str]: ...

    def env(self, job_dir: Path) -> dict[str, str]: ...

    def collect(self, job_dir: Path, return_code: int) -> tuple[list[dict[str, Any]], str | None]: ...

    def progress(self, record: dict[str, Any], log_path: Path | None, log_tail: str) -> dict[str, Any]: ...


def job_progress(record: dict[str, Any], log_path: Path | None, phase: str) -> dict[str, Any]:
    """Return the progress fields every provider reports, given its phase label."""

    status = record["status"]
    stop_time = time.time() if status in ACTIVE_STATUSES else record["updated_at"]
    return {
        "phase": phase,
        "elapsed_seconds": max(0, round(stop_time - record["created_at"])),
        "log_updated_at": log_path.stat().st_mtime if log_path and log_path.is_file() else None,
        "indeterminate": status in ACTIVE_STATUSES,
    }


def summarize_metrics(outputs: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    numeric: dict[str, list[float]] = {}
    for output in outputs:
        for key, value in output["metrics"].items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                numeric.setdefault(key, []).append(float(value))
    return {
        key: {"min": min(values), "max": max(values), "mean": sum(values) / len(values)}
        for key, values in numeric.items()
    }


def _signal_group(process: subprocess.Popen[str], signal_number: int) -> bool:
    """Signal the process group that process leads; return False once the group is gone."""

    try:
        os.killpg(process.pid, signal_number)
    except ProcessLookupError:
        return False
    return True


def _kill_after_grace(process: subprocess.Popen[str]) -> None:
    """SIGKILL the process group if any member outlives the grace period."""

    deadline = time.monotonic() + STOP_GRACE_SECONDS
    while time.monotonic() < deadline:
        # An unreaped leader still counts as a member of its group.
        process.poll()
        if not _signal_group(process, 0):
            return
        time.sleep(0.1)
    _signal_group(process, signal.SIGKILL)
    process.wait()


def stop_process_group(process: subprocess.Popen[str]) -> None:
    """SIGTERM a job's process group, then SIGKILL what remains after the grace period."""

    if _signal_group(process, signal.SIGTERM):
        _kill_after_grace(process)


class ProcessJobRunner:
    """Job records, admission, idempotency, retention, and subprocess lifecycle.

    A single worker thread runs one job at a time; max_active_jobs bounds queued plus
    running jobs. Each command starts in a new session, so cancellation and timeouts
    stop every process it spawned, and the next job starts only once all of them are
    gone. completed_at stays None until a job's outputs are final: cancel() marks a
    running job cancelled at once, and its outputs follow when its processes are gone.
    Subclasses add schema() and parse_request() to complete the JobProvider contract.
    """

    #: Program named in timeout errors.
    program_label = "the job"
    #: Error when max_active_jobs jobs are already queued or running.
    capacity_message = "the job queue is full; wait for an active job to finish"

    def __init__(
        self,
        hooks: ProcessJobHooks,
        *,
        root: Path | None,
        working_directory: Path | None = None,
        timeout_seconds: int = 4 * 60 * 60,
        max_active_jobs: int = 1,
        retention_seconds: int = 7 * 24 * 60 * 60,
    ) -> None:
        """Create a runner; working_directory defaults to each job's directory."""

        self.hooks = hooks
        self.root = root
        self.working_directory = working_directory
        self.timeout_seconds = max(1, timeout_seconds)
        self.max_active_jobs = max(1, max_active_jobs)
        self.retention_seconds = max(60 * 60, retention_seconds)
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"{hooks.id_prefix}-jobs")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)
            for record_path in self.root.glob("*/job.json"):
                try:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    if (
                        record.get("id") != record_path.parent.name
                        or record.get("provider") != self.hooks.provider
                        or record.get("status") not in ACTIVE_STATUSES | TERMINAL_STATUSES
                        or not isinstance(record.get("updated_at"), int | float)
                    ):
                        continue
                    if record.get("status") in ACTIVE_STATUSES:
                        record["status"] = "failed"
                        record["error"] = "service restarted while the job was running"
                        record["completed_at"] = time.time()
                        record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
                    # A loaded job's outputs are final, even if the service stopped before a
                    # cancelled job finished.
                    record["completed_at"] = record.get("completed_at") or record["updated_at"]
                    self._jobs[record["id"]] = record
                except (KeyError, OSError, ValueError):
                    continue
            self._prune()

    @property
    def enabled(self) -> bool:
        return self.root is not None

    def _write_record(self, record: dict[str, Any]) -> None:
        if self.root is None:
            return
        path = self.root / record["id"] / "job.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _prune(self) -> None:
        if self.root is None:
            return
        cutoff = time.time() - self.retention_seconds
        for job_id, record in list(self._jobs.items()):
            if record["status"] in TERMINAL_STATUSES and record["updated_at"] < cutoff:
                try:
                    shutil.rmtree(self.root / job_id)
                except OSError:
                    continue
                self._jobs.pop(job_id, None)

    def submit(
        self,
        cif: str,
        name: str,
        request: Any,
        *,
        source_session_id: str | None = None,
        owner_id: str = "local",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not self.enabled or self.root is None:
            raise RuntimeError(f"{self.hooks.id_prefix} jobs are not configured on this deployment")
        with self._lock:
            self._prune()
            fingerprint = self.hooks.fingerprint(cif, name, request)
            if idempotency_key:
                existing = next(
                    (
                        record
                        for record in self._jobs.values()
                        if record.get("owner_id", "local") == owner_id
                        and record.get("idempotency_key") == idempotency_key
                    ),
                    None,
                )
                if existing is not None:
                    if existing.get("request_fingerprint") != fingerprint:
                        raise IdempotencyConflictError(
                            f"Idempotency-Key was already used for a different {self.hooks.id_prefix} submission"
                        )
                    return copy.deepcopy(existing)
            active_jobs = sum(record["status"] in ACTIVE_STATUSES for record in self._jobs.values())
            if active_jobs >= self.max_active_jobs:
                raise CapacityExceededError(self.capacity_message)
            job_id = f"{self.hooks.id_prefix}-{int(time.time())}-{os.urandom(6).hex()}"
            job_dir = self.root / job_id
            job_dir.mkdir(mode=0o700)
            try:
                (job_dir / "input.cif").write_text(cif, encoding="utf-8")
                public_request = self.hooks.prepare(job_dir, cif, request)
            except BaseException:
                shutil.rmtree(job_dir, ignore_errors=True)
                raise
            now = time.time()
            record = {
                "id": job_id,
                "provider": self.hooks.provider,
                "status": "queued",
                "input_name": name,
                "source_session_id": source_session_id,
                "owner_id": owner_id,
                "idempotency_key": idempotency_key,
                "request_fingerprint": fingerprint,
                "created_at": now,
                "updated_at": now,
                "request": public_request,
                "outputs": [],
                "metric_summary": {},
                "error": None,
                "completed_at": None,
            }
            self._jobs[job_id] = record
            self._write_record(record)
            self._executor.submit(self._run, job_id)
            return copy.deepcopy(record)

    def _run(self, job_id: str) -> None:
        if self.root is None:
            return
        job_dir = self.root / job_id
        with self._lock:
            record = self._jobs[job_id]
            if record["status"] == "cancelled":
                return
            record["status"] = "running"
            record["updated_at"] = time.time()
            self._write_record(record)
        timeout_error: str | None = None
        try:
            with (job_dir / "job.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    self.hooks.command(job_dir),
                    cwd=self.working_directory or job_dir,
                    env={**os.environ, **self.hooks.env(job_dir)},
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                with self._lock:
                    self._processes[job_id] = process
                    # cancel() found no process to stop if it ran between "running" and Popen.
                    cancelled_while_starting = self._jobs[job_id]["status"] == "cancelled"
                if cancelled_while_starting:
                    self._stop_in_background(process)
                try:
                    return_code = process.wait(timeout=self.timeout_seconds)
                except subprocess.TimeoutExpired:
                    stop_process_group(process)
                    return_code = process.wait()
                    timeout_error = f"{self.program_label} exceeded its {self.timeout_seconds}s timeout"
                finally:
                    with self._lock:
                        self._processes.pop(job_id, None)
                # No process of this job may outlive it into the next job's turn.
                _kill_after_grace(process)
            outputs, error = self.hooks.collect(job_dir, return_code)
            error = timeout_error or error
        except Exception as exception:
            # A background job must always reach a terminal state for polling clients.
            outputs, error = [], str(exception)
        with self._lock:
            record = self._jobs[job_id]
            if record["status"] != "cancelled":
                record.update({"status": "failed" if error else "completed", "updated_at": time.time(), "error": error})
            # Failed and cancelled jobs keep whatever outputs their provider still reports.
            record["outputs"] = outputs
            record["metric_summary"] = summarize_metrics(outputs)
            record["completed_at"] = time.time()
            self._write_record(record)

    def get(self, job_id: str, *, owner_id: str = "local") -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            if self._jobs[job_id].get("owner_id", "local") != owner_id:
                raise KeyError(job_id)
            record = copy.deepcopy(self._jobs[job_id])
        log_path = self.root / job_id / "job.log" if self.root is not None else None
        raw_log = (
            log_path.read_text(encoding="utf-8", errors="replace")[-4000:] if log_path and log_path.is_file() else ""
        )
        record["log_tail"] = ANSI_ESCAPE.sub("", raw_log)
        record["progress"] = self.hooks.progress(record, log_path, record["log_tail"])
        return record

    def cancel(self, job_id: str, *, owner_id: str = "local") -> dict[str, Any]:
        """Cancel a queued or running job and stop only its own process group."""

        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            record = self._jobs[job_id]
            if record.get("owner_id", "local") != owner_id:
                raise KeyError(job_id)
            if record["status"] in TERMINAL_STATUSES:
                return copy.deepcopy(record)
            if record["status"] == "queued":
                # A job that never started has no outputs to wait for.
                record["completed_at"] = time.time()
            record["status"] = "cancelled"
            record["updated_at"] = time.time()
            record["error"] = None
            process = self._processes.get(job_id)
            self._write_record(record)
            result = copy.deepcopy(record)
        if process is not None:
            self._stop_in_background(process)
        return result

    @staticmethod
    def _stop_in_background(process: subprocess.Popen[str]) -> None:
        """SIGTERM the process group now and escalate to SIGKILL from a daemon thread."""

        if _signal_group(process, signal.SIGTERM):
            threading.Thread(target=_kill_after_grace, args=(process,), daemon=True).start()

    def _output(self, job_id: str, output_id: str, owner_id: str) -> dict[str, Any]:
        record = self.get(job_id, owner_id=owner_id)
        output = next((item for item in record["outputs"] if item["id"] == output_id), None)
        if output is None:
            raise KeyError(output_id)
        return output

    def _confined(self, job_id: str, path: Path) -> Path:
        resolved = path.resolve()
        if self.root is None or not resolved.is_relative_to((self.root / job_id).resolve()):
            raise ValueError("job output path escaped its workspace")
        return resolved

    def output_cif(
        self,
        job_id: str,
        output_id: str,
        *,
        owner_id: str = "local",
    ) -> tuple[str, dict[str, Any]]:
        output = self._output(job_id, output_id, owner_id)
        return self._confined(job_id, Path(output["cif_path"])).read_text(encoding="utf-8"), output

    def input_cif(self, job_id: str, *, owner_id: str = "local") -> str:
        self.get(job_id, owner_id=owner_id)
        if self.root is None:
            raise KeyError(job_id)
        return (self.root / job_id / "input.cif").read_text(encoding="utf-8")

    def artifact(self, job_id: str, output_id: str, name: str, *, owner_id: str = "local") -> tuple[Path, str]:
        """Return an output's artifact file, confined to the job directory, and its media type."""

        value = self._output(job_id, output_id, owner_id).get("artifacts", {}).get(name)
        if value is None:
            raise KeyError(name)
        path = self._confined(job_id, Path(value))
        if not path.is_file():
            raise KeyError(name)
        return path, ARTIFACT_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")

    def close(self) -> None:
        with self._lock:
            processes = list(self._processes.values())
        for process in processes:
            self._stop_in_background(process)
        self._executor.shutdown(wait=False, cancel_futures=True)
