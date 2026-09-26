"""Read ``outputs/manifest.json``, which ``aq job run`` writes last, into Studio job outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from annotation_studio.process_jobs import ANSI_ESCAPE
from pydantic import BaseModel, Field

# Characters of job.log quoted when aq wrote no manifest.
LOG_EXCERPT_CHARS = 1000


class ManifestError(BaseModel):
    type: str
    message: str


class ManifestOutput(BaseModel):
    """One finished stage; paths are relative to the job directory."""

    id: str
    label: str
    kind: str
    rank: int
    structure: str
    metrics: dict[str, bool | int | float] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)


class Manifest(BaseModel):
    format: Literal["atomworks-quantum.job-manifest/1"] = Field(alias="schema")
    status: Literal["completed", "unconverged", "failed", "cancelled"]
    error: ManifestError | None = None
    outputs: list[ManifestOutput]


def collect_outputs(job_dir: Path, return_code: int) -> tuple[list[dict[str, Any]], str | None]:
    """Return the outputs of every finished stage and an error message, None when the job succeeded.

    An unconverged job succeeds: its outputs carry ``converged: false`` in their metrics. Failed and
    cancelled jobs keep the outputs of the stages that finished.
    """

    path = job_dir / "outputs" / "manifest.json"
    try:
        manifest = Manifest.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        return [], f"aq exited with status {return_code} without writing outputs/manifest.json{_log_excerpt(job_dir)}"
    except (OSError, ValueError) as error:
        return [], f"aq wrote an unreadable outputs/manifest.json: {error}"
    outputs = [
        {
            "id": output.id,
            "label": output.label,
            "kind": output.kind,
            "rank": output.rank,
            "cif_path": str(job_dir / output.structure),
            "metrics": output.metrics,
            "units": output.units,
            "artifacts": {name: str(job_dir / relative) for name, relative in output.artifacts.items()},
        }
        for output in manifest.outputs
    ]
    if manifest.status in ("completed", "unconverged"):
        return outputs, None
    if manifest.error is None:
        return outputs, f"aq job {manifest.status} with exit status {return_code}"
    return outputs, f"{manifest.error.type}: {manifest.error.message}"


def _log_excerpt(job_dir: Path) -> str:
    try:
        log = (job_dir / "job.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    excerpt = ANSI_ESCAPE.sub("", log[-LOG_EXCERPT_CHARS:]).strip()
    return f"; last output:\n{excerpt}" if excerpt else ""
