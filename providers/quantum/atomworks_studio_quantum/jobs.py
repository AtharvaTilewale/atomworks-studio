"""AtomWorks-Quantum jobs: ``aq job run`` on a job directory, in aq's own environment, one process per job."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from annotation_studio.process_jobs import ProcessJobRunner, job_progress

from .manifest import collect_outputs
from .request import JOB_SCHEMA, QuantumJobRequest

PROVIDER = "quantum"
PROVIDER_LABEL = "AtomWorks-Quantum"
# Seconds allowed for aq job validate, aq job schema and aq models.
AQ_TIMEOUT_SECONDS = 30
# Seconds the model catalog and request schema (or the failure to read them) are reused.
CATALOG_TTL_SECONDS = 10 * 60
# Model catalog fields the browser sees; the others can hold server paths (source, options).
MODEL_FIELDS = ("name", "backend", "charge", "spin", "elements", "devices", "hessian", "license")
STAGE_LINE = re.compile(r"^\[aq\] stage (\d+)/(\d+) (?:\d+-)?(\S+) \w+$", re.MULTILINE)


class QuantumJobHooks:
    """Job files, ``aq job run`` command, manifest outputs, and progress for ProcessJobRunner."""

    provider = PROVIDER
    id_prefix = "quantum"

    def __init__(self, command: list[str]) -> None:
        self.command_prefix = command

    def prepare(self, job_dir: Path, cif: str, request: QuantumJobRequest) -> dict[str, Any]:
        del cif
        job = request.job()
        (job_dir / "request.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
        return job

    def fingerprint(self, cif: str, name: str, request: QuantumJobRequest) -> str:
        payload = json.dumps(request.job(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256("\0".join((cif, name, payload)).encode()).hexdigest()

    def command(self, job_dir: Path) -> list[str]:
        return [*self.command_prefix, "job", "run", str(job_dir)]

    def env(self, job_dir: Path) -> dict[str, str]:
        # aq inherits the API environment (OMP_NUM_THREADS, CUDA_VISIBLE_DEVICES, AQ_*_COMMAND, HF_HOME, ...).
        del job_dir
        return {}

    def collect(self, job_dir: Path, return_code: int) -> tuple[list[dict[str, Any]], str | None]:
        return collect_outputs(job_dir, return_code)

    def progress(self, record: dict[str, Any], log_path: Path | None, log_tail: str) -> dict[str, Any]:
        status = record["status"]
        phase = {"queued": "Queued", "completed": "Completed", "failed": "Failed", "cancelled": "Cancelled"}.get(
            status, "Starting aq"
        )
        if status == "running" and (stages := STAGE_LINE.findall(log_tail)):
            rank, count, name = stages[-1]
            phase = f"Stage {rank}/{count}: {name}"
        return job_progress(record, log_path, phase)


class QuantumJobProvider(ProcessJobRunner):
    """Runs aq jobs from the browser; enabled when an aq command and a job directory are configured."""

    program_label = "aq"
    capacity_message = "the quantum job queue is full; wait for an active job to finish"

    def __init__(
        self,
        *,
        root: Path | None,
        command: list[str] | None,
        models: frozenset[str] | None = None,
        timeout_seconds: int = 4 * 60 * 60,
        max_active_jobs: int = 1,
        retention_seconds: int = 7 * 24 * 60 * 60,
    ) -> None:
        """Create the provider; models, when given, is the allowlist of catalog names offered and accepted."""

        self.command = command
        self.models = models
        self._catalog_lock = threading.Lock()
        self._catalog: tuple[float, dict[str, Any] | RuntimeError] | None = None
        super().__init__(
            QuantumJobHooks(command or []),
            root=root,
            timeout_seconds=timeout_seconds,
            max_active_jobs=max_active_jobs,
            retention_seconds=retention_seconds,
        )

    @classmethod
    def from_environment(cls) -> QuantumJobProvider:
        root = os.environ.get("ANNOTATION_STUDIO_QUANTUM_JOB_DIR")
        if not root and (studio_jobs := os.environ.get("ANNOTATION_STUDIO_JOB_DIR")):
            root = str(Path(studio_jobs) / "quantum")
        models = os.environ.get("ANNOTATION_STUDIO_QUANTUM_MODELS")
        return cls(
            root=Path(root) if root else None,
            command=_command_setting("ANNOTATION_STUDIO_QUANTUM_COMMAND"),
            models=frozenset(filter(None, (name.strip() for name in models.split(",")))) if models else None,
            timeout_seconds=_integer_setting("ANNOTATION_STUDIO_QUANTUM_TIMEOUT_SECONDS", 4 * 60 * 60),
            max_active_jobs=_integer_setting("ANNOTATION_STUDIO_QUANTUM_MAX_ACTIVE_JOBS", 1),
            retention_seconds=_integer_setting("ANNOTATION_STUDIO_JOB_RETENTION_SECONDS", 7 * 24 * 60 * 60),
        )

    @property
    def enabled(self) -> bool:
        return self.root is not None and bool(self.command)

    def schema(self) -> dict[str, Any]:
        """The form data: tasks, offered models, defaults and aq's request schema, or why jobs cannot run."""

        schema: dict[str, Any] = {"enabled": False, "provider": PROVIDER, "label": PROVIDER_LABEL}
        if not self.command:
            return {**schema, "reason": "ANNOTATION_STUDIO_QUANTUM_COMMAND is not set"}
        if self.root is None:
            return {
                **schema,
                "reason": "neither ANNOTATION_STUDIO_QUANTUM_JOB_DIR nor ANNOTATION_STUDIO_JOB_DIR is set",
            }
        with self._catalog_lock:
            if self._catalog is None or time.monotonic() - self._catalog[0] > CATALOG_TTL_SECONDS:
                try:
                    catalog: dict[str, Any] | RuntimeError = self._read_catalog()
                except RuntimeError as error:
                    catalog = error
                self._catalog = (time.monotonic(), catalog)
            catalog = self._catalog[1]
        if isinstance(catalog, RuntimeError):
            return {**schema, "reason": str(catalog)}
        return {**schema, "enabled": True, **catalog}

    def parse_request(self, payload: dict[str, Any]) -> QuantumJobRequest:
        """Validate a request here and with ``aq job validate``; ValueError when it is invalid."""

        if not self.enabled:
            raise RuntimeError(f"{self.hooks.id_prefix} jobs are not configured on this deployment")
        request = QuantumJobRequest.model_validate(payload)
        if self.models is not None and (refused := sorted(request.models() - self.models)):
            raise ValueError(
                f"model(s) {', '.join(refused)} are not offered on this deployment; "
                f"allowed models: {', '.join(sorted(self.models)) or 'none'}"
            )
        result = self._aq("job", "validate", "-", stdin=json.dumps(request.job()))
        if result.returncode == 2:
            raise ValueError(result.stderr.strip() or "aq job validate refused the request")
        if result.returncode != 0:
            raise RuntimeError(f"aq job validate failed with exit status {result.returncode}: {_last_line(result)}")
        return request

    def _read_catalog(self) -> dict[str, Any]:
        rows = self._aq_json("models", "--json", "--installed")
        request_schema = self._aq_json("job", "schema")
        try:
            models = [
                {key: row.get(key) for key in MODEL_FIELDS}
                for row in rows
                if (self.models is None or row["name"] in self.models) and _runs_without_options(row)
            ]
            tasks = list(request_schema["properties"]["stages"]["items"]["discriminator"]["mapping"])
        except (KeyError, TypeError, AttributeError) as error:
            raise RuntimeError(
                f"aq printed a model catalog or job schema this provider cannot read: {error!r}"
            ) from None
        if not models:
            allowlist = "" if self.models is None else " that ANNOTATION_STUDIO_QUANTUM_MODELS allows"
            raise RuntimeError(f"aq has no installed model{allowlist}")
        defaults = {
            "schema": JOB_SCHEMA,
            "charge": 0,
            "multiplicity": 1,
            "calculator": models[0]["name"],
            "stages": [{"task": "singlepoint"}],
        }
        return {"tasks": tasks, "models": models, "defaults": defaults, "request_schema": request_schema}

    def _aq_json(self, *arguments: str) -> Any:
        result = self._aq(*arguments)
        if result.returncode != 0:
            raise RuntimeError(
                f"aq {' '.join(arguments)} failed with exit status {result.returncode}: {_last_line(result)}"
            )
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise RuntimeError(f"aq {' '.join(arguments)} printed invalid JSON: {error}") from None

    def _aq(self, *arguments: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        argv = [*(self.command or []), *arguments]
        try:
            return subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=AQ_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"aq {' '.join(arguments)} did not finish within {AQ_TIMEOUT_SECONDS}s") from None
        except OSError as error:
            raise RuntimeError(f"could not run {shlex.join(argv)}: {error}") from None


def _runs_without_options(row: dict[str, Any]) -> bool:
    """Whether the model's card supplies every option aq requires for it; Studio requests carry no options.

    aq publishes them as ``required_options`` (a method and a basis, ORCA's keywords, a MACE head); a catalog
    without that field requires none.
    """
    return set(row.get("required_options") or {}) <= set(row.get("options") or {})


def _last_line(result: subprocess.CompletedProcess[str]) -> str:
    lines = (result.stderr or result.stdout).strip().splitlines()
    return lines[-1] if lines else "no output"


def _command_setting(name: str) -> list[str] | None:
    value = os.environ.get(name)
    if not value:
        return None
    try:
        argv = shlex.split(value)
    except ValueError as error:
        raise RuntimeError(f"{name} is not a valid command line: {error}") from None
    # aq job run starts in the job directory, while aq job validate and aq models start in the API's directory.
    if argv and os.sep in argv[0] and not os.path.isabs(argv[0]):
        raise RuntimeError(
            f"{name} must start with an absolute path or a program on PATH, because aq runs in each job directory; "
            f"got {argv[0]!r}"
        )
    return argv or None


def _integer_setting(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer; got {value!r}") from None
