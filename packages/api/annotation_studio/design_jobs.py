"""Provider-neutral RFD4 design jobs for local GPU and Brev environments."""

from __future__ import annotations

import copy
import csv
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
ALLOWED_CONFIG_KEYS = {
    "batch_size",
    "ckpt_name",
    "generation",
    "metrics_tags",
    "num_replicates",
    "search",
    "seed",
}
SEARCH_STRATEGY_TARGET = "rfproteina.search.beam_search.BeamSearchStrategy"
AF3_ENVIRONMENT_KEYS = ("AF3_APPTAINER_PATH", "AF3_SCRIPTS_DIR", "AF3_JAX_CACHE_DIR", "AF3_MODEL_DIR")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _job_progress(record: dict[str, Any], log_path: Path | None, log_tail: str) -> dict[str, Any]:
    status = record["status"]
    phase = {
        "queued": "Waiting for GPU",
        "completed": "Completed",
        "failed": "Failed",
        "cancelled": "Cancelled",
    }.get(status, "Starting RFD4")
    if status == "running":
        lowered = log_tail.lower()
        milestones = (
            ("generation complete", "Collecting outputs"),
            ("saved per_example_metrics", "Collecting outputs"),
            ("metrics-pool", "Computing metrics"),
            ("validation [", "Sampling designs"),
            ("steps/s=", "Sampling designs"),
            ("torch.compile", "Compiling kernels"),
            ("compiling autoencoder", "Compiling kernels"),
            ("loading checkpoint", "Loading models"),
        )
        latest = max(
            ((lowered.rfind(marker), label) for marker, label in milestones),
            default=(-1, phase),
        )
        if latest[0] >= 0:
            phase = latest[1]
    stop_time = time.time() if status in {"queued", "running"} else record["updated_at"]
    return {
        "phase": phase,
        "elapsed_seconds": max(0, round(stop_time - record["created_at"])),
        "log_updated_at": log_path.stat().st_mtime if log_path and log_path.is_file() else None,
        "indeterminate": status in {"queued", "running"},
    }


class SearchOptions(BaseModel):
    step_checkpoints: list[int] = Field(default_factory=list, min_length=2, max_length=32)
    n_branch: int = Field(default=2, ge=1, le=16)
    beam_width: int = Field(default=2, ge=1, le=16)
    reward_metrics_tags: list[str] = Field(default_factory=lambda: ["interchain_interactions"], max_length=32)
    weights: dict[str, float] = Field(
        default_factory=lambda: {"interchain_interactions.hbond_atom_pair_count_coi": 1.0},
        min_length=1,
        max_length=32,
    )

    @field_validator("reward_metrics_tags")
    @classmethod
    def valid_reward_tags(cls, values: list[str]) -> list[str]:
        if any(SAFE_NAME.fullmatch(value) is None for value in values):
            raise ValueError("reward metric tags may contain only letters, numbers, '.', '_', and '-'")
        return values

    @field_validator("weights")
    @classmethod
    def valid_weights(cls, values: dict[str, float]) -> dict[str, float]:
        if any(SAFE_NAME.fullmatch(key) is None for key in values):
            raise ValueError("reward keys may contain only letters, numbers, '.', '_', and '-'")
        return values


class DesignJobRequest(BaseModel):
    mode: Literal["generate", "search"] = "generate"
    num_designs: int = Field(default=8, ge=1, le=64)
    batch_size: int = Field(default=4, ge=1, le=32)
    steps: int = Field(default=100, ge=1, le=1000)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    metrics_tags: list[str] = Field(default_factory=lambda: ["design"], max_length=32)
    search: SearchOptions | None = None
    config_yaml: str | None = Field(default=None, max_length=128 * 1024)

    @field_validator("metrics_tags")
    @classmethod
    def valid_metric_tags(cls, values: list[str]) -> list[str]:
        if any(SAFE_NAME.fullmatch(value) is None for value in values):
            raise ValueError("metric tags may contain only letters, numbers, '.', '_', and '-'")
        return values


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _reject_unsafe_config(value: Any, path: str = "config") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key.startswith("_"):
                raise ValueError(f"{path} contains a reserved key")
            _reject_unsafe_config(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_unsafe_config(item, f"{path}[{index}]")
    elif isinstance(value, str) and ("${" in value or "\x00" in value):
        raise ValueError(f"{path} contains interpolation or control characters")


def _uploaded_options(config_yaml: str | None) -> dict[str, Any]:
    if not config_yaml:
        return {}
    try:
        loaded = yaml.safe_load(config_yaml)
    except yaml.YAMLError as error:
        raise ValueError(f"invalid model-options YAML: {error}") from error
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise TypeError("uploaded model options must be a YAML mapping")
    unknown = set(loaded) - ALLOWED_CONFIG_KEYS
    if unknown:
        raise ValueError(f"unsupported model option(s): {', '.join(sorted(unknown))}")
    _reject_unsafe_config(loaded)
    return loaded


def _bounded_int(config: dict[str, Any], key: str, minimum: int, maximum: int) -> int:
    value = config.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{key} must be an integer between {minimum} and {maximum}")
    return value


def _validate_resolved_config(config: dict[str, Any]) -> None:
    _bounded_int(config, "num_replicates", 1, 64)
    _bounded_int(config, "batch_size", 1, 32)
    seed = config.get("seed")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2**31 - 1):
        raise ValueError("seed must be null or a non-negative 32-bit integer")
    generation = config.get("generation")
    if not isinstance(generation, dict) or not isinstance(generation.get("args"), dict):
        raise TypeError("generation.args must be a mapping")
    steps = generation["args"].get("nsteps")
    if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= 1000:
        raise ValueError("generation.args.nsteps must be an integer between 1 and 1000")
    tags = config.get("metrics_tags", [])
    if (
        not isinstance(tags, list)
        or len(tags) > 32
        or any(not isinstance(tag, str) or SAFE_NAME.fullmatch(tag) is None for tag in tags)
    ):
        raise ValueError("metrics_tags must be a list of safe tag names")
    if (checkpoint := config.get("ckpt_name")) and (
        not isinstance(checkpoint, str) or SAFE_NAME.fullmatch(checkpoint) is None
    ):
        raise ValueError("ckpt_name must be a filename, not a path")
    if search := config.get("search"):
        strategy = search["strategy"]
        for key in ("n_branch", "beam_width"):
            value = strategy.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 16:
                raise ValueError(f"search.strategy.{key} must be between 1 and 16")
        rewards = search["rewards"]
        reward_tags = rewards.get("reward_metrics_tags", [])
        if (
            not isinstance(reward_tags, list)
            or len(reward_tags) > 32
            or any(not isinstance(tag, str) or SAFE_NAME.fullmatch(tag) is None for tag in reward_tags)
        ):
            raise ValueError("search reward_metrics_tags must be a list of safe tag names")
        weights = rewards.get("weights")
        if (
            not isinstance(weights, dict)
            or not weights
            or len(weights) > 32
            or any(
                not isinstance(key, str)
                or SAFE_NAME.fullmatch(key) is None
                or not isinstance(value, int | float)
                or isinstance(value, bool)
                for key, value in weights.items()
            )
        ):
            raise ValueError("search reward weights must map safe metric names to numbers")
        clip_values = rewards.get("clip_values", {})
        if not isinstance(clip_values, dict) or any(
            key not in weights
            or not isinstance(bounds, dict)
            or set(bounds) - {"min", "max"}
            or any(not isinstance(bound, int | float) or isinstance(bound, bool) for bound in bounds.values())
            for key, bounds in clip_values.items()
        ):
            raise ValueError("search clip_values must contain numeric min/max bounds for weighted metrics")


def build_rfd4_config(
    request: DesignJobRequest,
    *,
    input_path: Path,
    output_dir: Path,
    run_name: str,
    project_root: Path,
) -> dict[str, Any]:
    """Build a safe external Hydra config while keeping paths server-controlled."""

    config: dict[str, Any] = {
        "defaults": ["inference", "_self_"],
        "inputs": str(input_path),
        "out_dir": str(output_dir),
        "run_name_": run_name,
        "num_replicates": request.num_designs,
        "batch_size": request.batch_size,
        "seed": request.seed,
        "metrics_tags": request.metrics_tags,
        "generation": {"args": {"nsteps": request.steps}},
        "hydra": {"searchpath": [f"file://{project_root / 'configs'}"]},
    }
    config = _deep_merge(config, _uploaded_options(request.config_yaml))
    if not isinstance(config.get("generation"), dict) or not isinstance(config["generation"].get("args"), dict):
        raise TypeError("generation.args must be a mapping")

    search_options = request.search
    if request.mode == "search" or "search" in config:
        raw_search = config.pop("search", {})
        if not isinstance(raw_search, dict):
            raise ValueError("search options must be a mapping")
        strategy_values = raw_search.get("strategy", {})
        reward_values = raw_search.get("rewards", {})
        if not isinstance(strategy_values, dict) or not isinstance(reward_values, dict):
            raise ValueError("search.strategy and search.rewards must be mappings")
        unknown_strategy = set(strategy_values) - {"beam_width", "n_branch", "step_checkpoints"}
        unknown_rewards = set(reward_values) - {"clip_values", "reward_metrics_tags", "weights"}
        if unknown_strategy or unknown_rewards:
            unknown = sorted(unknown_strategy | unknown_rewards)
            raise ValueError(f"unsupported search option(s): {', '.join(unknown)}")
        if search_options is None:
            search_options = SearchOptions(step_checkpoints=[0, request.steps])
        structured = search_options.model_dump()
        strategy = _deep_merge(
            {
                "step_checkpoints": structured.pop("step_checkpoints"),
                "n_branch": structured.pop("n_branch"),
                "beam_width": structured.pop("beam_width"),
            },
            strategy_values,
        )
        rewards = _deep_merge(
            {
                "reward_metrics_tags": structured.pop("reward_metrics_tags"),
                "weights": structured.pop("weights"),
            },
            reward_values,
        )
        checkpoints = strategy.get("step_checkpoints")
        steps = int(config.get("generation", {}).get("args", {}).get("nsteps", request.steps))
        if (
            not isinstance(checkpoints, list)
            or len(checkpoints) < 2
            or any(not isinstance(value, int) or isinstance(value, bool) for value in checkpoints)
            or checkpoints[0] != 0
            or checkpoints[-1] != steps
            or checkpoints != sorted(set(checkpoints))
        ):
            raise ValueError("search checkpoints must start at 0, increase strictly, and end at nsteps")
        config["search"] = {
            "strategy": {"_target_": SEARCH_STRATEGY_TARGET, **strategy},
            "rewards": rewards,
        }

    # Uploaded files may tune models, sampling, and search, but never control I/O or Hydra execution.
    config["inputs"] = str(input_path)
    config["out_dir"] = str(output_dir)
    config["run_name_"] = run_name
    config["hydra"] = {"searchpath": [f"file://{project_root / 'configs'}"]}
    _validate_resolved_config(config)
    return config


def _csv_value(value: str) -> Any:
    if value == "":
        return None
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        return int(number) if number.is_integer() else number
    except ValueError:
        return value


def _metric_rows(output_dir: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted((output_dir / "inference_metrics").glob("*per_example_metrics*.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                parsed = {key: _csv_value(value) for key, value in row.items() if key}
                if example_id := parsed.get("example_id"):
                    rows[str(example_id)] = parsed
    return rows


def collect_outputs(output_dir: Path) -> list[dict[str, Any]]:
    metrics = _metric_rows(output_dir)
    outputs: list[dict[str, Any]] = []
    for path in sorted((output_dir / "inference_samples").glob("**/*_generated.cif")):
        example_id = path.name.removesuffix("_generated.cif")
        example_metrics = metrics.get(example_id, {})
        outputs.append(
            {
                "id": example_id,
                "label": example_id.split("__", 1)[-1],
                "kind": "generated",
                "cif_path": str(path),
                "metrics": example_metrics,
            }
        )
        for key, value in example_metrics.items():
            if not key.endswith("refolded_structure_path") or not isinstance(value, str):
                continue
            refolded = Path(value)
            try:
                in_workspace = refolded.resolve().is_relative_to(output_dir.resolve())
            except OSError:
                in_workspace = False
            if in_workspace and refolded.is_file() and refolded.suffix.lower() in {".cif", ".mmcif"}:
                oracle = key.removesuffix(".refolded_structure_path").removesuffix("_structure_path")
                outputs.append(
                    {
                        "id": f"{example_id}--{oracle or 'oracle'}-refolded",
                        "label": f"{example_id.split('__', 1)[-1]} · {oracle or 'oracle'} refolded",
                        "kind": "refolded",
                        "cif_path": str(refolded),
                        "metrics": example_metrics,
                    }
                )

    def score(output: dict[str, Any]) -> float:
        values = output["metrics"]
        for key in ("search.reward", "oracle.plddt_coi", "oracle.overall_plddt", "plddt"):
            if isinstance(values.get(key), int | float):
                return float(values[key])
        return float("-inf")

    outputs.sort(key=lambda output: (-score(output), output["label"]))
    for rank, output in enumerate(outputs, 1):
        output["rank"] = rank
    return outputs


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


class ProcessDesignProvider:
    """Single-GPU provider that invokes RFD4 through an argv-only subprocess."""

    def __init__(
        self,
        *,
        root: Path | None,
        project_root: Path,
        command: list[str] | None,
        timeout_seconds: int = 4 * 60 * 60,
        max_active_jobs: int = 1,
        retention_seconds: int = 7 * 24 * 60 * 60,
    ) -> None:
        self.root = root
        self.project_root = project_root
        self.command = command
        self.timeout_seconds = max(1, timeout_seconds)
        self.max_active_jobs = max(1, max_active_jobs)
        self.retention_seconds = max(60 * 60, retention_seconds)
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rfd4-design")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)
            for record_path in self.root.glob("*/job.json"):
                try:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    if (
                        record.get("id") != record_path.parent.name
                        or record.get("status") not in {"queued", "running", "completed", "failed", "cancelled"}
                        or not isinstance(record.get("updated_at"), int | float)
                    ):
                        continue
                    if record.get("status") in {"queued", "running"}:
                        record["status"] = "failed"
                        record["error"] = "service restarted while the job was running"
                        record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
                    self._jobs[record["id"]] = record
                except (KeyError, OSError, ValueError):
                    continue
            self._prune()

    @classmethod
    def from_environment(cls, default_project_root: Path) -> ProcessDesignProvider:
        root_value = os.environ.get("ANNOTATION_STUDIO_JOB_DIR")
        command_value = os.environ.get("ANNOTATION_STUDIO_RFD4_COMMAND")
        project_root = Path(os.environ.get("ANNOTATION_STUDIO_RFD4_ROOT", default_project_root))
        return cls(
            root=Path(root_value) if root_value else None,
            project_root=project_root,
            command=shlex.split(command_value) if command_value else None,
            timeout_seconds=int(os.environ.get("ANNOTATION_STUDIO_JOB_TIMEOUT_SECONDS", str(4 * 60 * 60))),
            max_active_jobs=int(os.environ.get("ANNOTATION_STUDIO_MAX_ACTIVE_JOBS", "1")),
            retention_seconds=int(os.environ.get("ANNOTATION_STUDIO_JOB_RETENTION_SECONDS", str(7 * 24 * 60 * 60))),
        )

    @property
    def enabled(self) -> bool:
        return self.root is not None and bool(self.command)

    def schema(self) -> dict[str, Any]:
        af3_enabled = bool(os.environ.get("AF3_LAUNCHER_SOCKET_TEMPLATE")) or all(
            os.environ.get(key) for key in AF3_ENVIRONMENT_KEYS
        )
        metrics_tags = [
            "design",
            "unindexed",
            "3d_coordinate_conditioning",
            "hotspot_conditioning",
            "interchain_interactions",
            "rasa_conditioning",
        ]
        if af3_enabled:
            metrics_tags.extend(
                [
                    "ppi_refolding",
                    "ame_refolding",
                    "disco_refolding",
                    "motif_scaffold_refolding",
                    "sm_binder_refolding",
                    "unconditional_refolding",
                ]
            )
        defaults = DesignJobRequest().model_dump()
        defaults["search"] = SearchOptions(step_checkpoints=[0, 50, 100]).model_dump()
        return {
            "enabled": self.enabled,
            "provider": "local-rfd4",
            "capabilities": {"af3": af3_enabled},
            "defaults": defaults,
            "limits": {"num_designs": 64, "batch_size": 32, "steps": 1000},
            "metrics_tags": metrics_tags,
        }

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
            if record["status"] in {"completed", "failed", "cancelled"} and record["updated_at"] < cutoff:
                try:
                    shutil.rmtree(self.root / job_id)
                except OSError:
                    continue
                self._jobs.pop(job_id, None)

    def submit(
        self,
        cif: str,
        name: str,
        request: DesignJobRequest,
        *,
        source_session_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.enabled or self.root is None:
            raise RuntimeError("design jobs are not configured on this deployment")
        with self._lock:
            self._prune()
            active_jobs = sum(record["status"] in {"queued", "running"} for record in self._jobs.values())
            if active_jobs >= self.max_active_jobs:
                raise RuntimeError("the GPU queue is full; wait for an active job to finish")
            job_id = f"design-{int(time.time())}-{os.urandom(6).hex()}"
            job_dir = self.root / job_id
            input_path = job_dir / "input.cif"
            output_dir = job_dir / "outputs"
            config = build_rfd4_config(
                request,
                input_path=input_path,
                output_dir=output_dir,
                run_name=job_id,
                project_root=self.project_root,
            )
            job_dir.mkdir(mode=0o700)
            input_path.write_text(cif, encoding="utf-8")
            (job_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
            now = time.time()
            record = {
                "id": job_id,
                "provider": "local-rfd4",
                "status": "queued",
                "input_name": name,
                "source_session_id": source_session_id,
                "created_at": now,
                "updated_at": now,
                "request": request.model_dump(exclude={"config_yaml"}),
                "outputs": [],
                "metric_summary": {},
                "error": None,
            }
            self._jobs[job_id] = record
            self._write_record(record)
            self._executor.submit(self._run, job_id)
            return copy.deepcopy(record)

    def _run(self, job_id: str) -> None:
        if self.root is None or self.command is None:
            return
        job_dir = self.root / job_id
        with self._lock:
            record = self._jobs[job_id]
            if record["status"] == "cancelled":
                return
            record["status"] = "running"
            record["updated_at"] = time.time()
            self._write_record(record)
        command = [
            *self.command,
            "--config-path",
            str(job_dir),
            "--config-name",
            "config",
        ]
        try:
            with (job_dir / "job.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    command,
                    cwd=self.project_root,
                    env={**os.environ, "PROJECT_ROOT": str(self.project_root)},
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                with self._lock:
                    self._processes[job_id] = process
                try:
                    return_code = process.wait(timeout=self.timeout_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise RuntimeError(f"RFD4 exceeded its {self.timeout_seconds}s timeout") from None
                finally:
                    with self._lock:
                        self._processes.pop(job_id, None)
            if return_code != 0:
                raise RuntimeError(f"RFD4 exited with status {return_code}")
            outputs = collect_outputs(job_dir / "outputs")
            if not outputs:
                raise RuntimeError("RFD4 completed without generated CIF outputs")
            status = "completed"
            error = None
        except Exception as exception:
            # A background job must always reach a terminal state for polling clients.
            outputs = []
            status = "failed"
            error = str(exception)
        with self._lock:
            record = self._jobs[job_id]
            if record["status"] == "cancelled":
                self._write_record(record)
                return
            record.update(
                {
                    "status": status,
                    "updated_at": time.time(),
                    "outputs": outputs,
                    "metric_summary": summarize_metrics(outputs),
                    "error": error,
                }
            )
            self._write_record(record)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            record = copy.deepcopy(self._jobs[job_id])
        log_path = self.root / job_id / "job.log" if self.root is not None else None
        raw_log = (
            log_path.read_text(encoding="utf-8", errors="replace")[-4000:] if log_path and log_path.is_file() else ""
        )
        record["log_tail"] = ANSI_ESCAPE.sub("", raw_log)
        record["progress"] = _job_progress(record, log_path, record["log_tail"])
        return record

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel a queued or running job and terminate only its owned subprocess."""

        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            record = self._jobs[job_id]
            if record["status"] in {"completed", "failed", "cancelled"}:
                return copy.deepcopy(record)
            record["status"] = "cancelled"
            record["updated_at"] = time.time()
            record["error"] = None
            process = self._processes.get(job_id)
            self._write_record(record)
            result = copy.deepcopy(record)
        if process is not None and process.poll() is None:
            process.terminate()
        return result

    def output_cif(self, job_id: str, output_id: str) -> tuple[str, dict[str, Any]]:
        record = self.get(job_id)
        output = next((item for item in record["outputs"] if item["id"] == output_id), None)
        if output is None:
            raise KeyError(output_id)
        path = Path(output["cif_path"])
        if self.root is None or not path.resolve().is_relative_to((self.root / job_id).resolve()):
            raise ValueError("job output path escaped its workspace")
        return path.read_text(encoding="utf-8"), output

    def input_cif(self, job_id: str) -> str:
        self.get(job_id)
        if self.root is None:
            raise KeyError(job_id)
        return (self.root / job_id / "input.cif").read_text(encoding="utf-8")

    def close(self) -> None:
        with self._lock:
            processes = list(self._processes.values())
        for process in processes:
            if process.poll() is None:
                process.terminate()
        self._executor.shutdown(wait=False, cancel_futures=True)
