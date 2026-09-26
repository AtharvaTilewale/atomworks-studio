"""Tiny command-compatible aq stand-in used only by Annotation Studio tests.

Implements ``aq job run DIR``, ``aq job validate -``, ``aq job schema`` and ``aq models --json --installed``.
``job validate`` refuses unknown models and tasks, and a model whose card lacks a required option (Studio requests
set none); mace-off23-small is printed without ``required_options``, as by an aq from before that field.
Environment knobs: FAKE_AQ_CALLS appends each argv to that file; FAKE_AQ_BROKEN=1 makes every command crash;
FAKE_AQ_RUN picks how ``job run`` ends: completed (default), unconverged, failed (manifest), failed-without-error
(a failed manifest without an error object), invalid (a manifest aq cannot have written), crash (no manifest),
escape (outputs that point outside the job), or slow (waits for SIGTERM with a child process).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

MODELS = [
    {"name": "gfn2-xtb", "backend": "tblite", "devices": ["cpu"], "options": {}, "required_options": {}},
    {"name": "mace-off23-small", "backend": "mace", "devices": ["cpu", "cuda"], "source": "/models/mace.model"},
    {
        "name": "orca",
        "backend": "orca",
        "devices": ["cpu"],
        "options": {"input": "r2SCAN-3c"},
        "required_options": {"input": "r2SCAN-3c"},
    },
    {
        "name": "pyscf",
        "backend": "pyscf",
        "devices": ["cpu"],
        "options": {},
        "required_options": {"method": "b3lyp-d3bj", "basis": "def2-svp"},
    },
    {
        "name": "mace-mh-1",
        "backend": "mace",
        "devices": ["cpu", "cuda"],
        "options": {},
        "required_options": {"head": "omol"},
    },
]
TASKS = ("frequencies", "md", "optimize", "singlepoint")
SCHEMA = {
    "title": "JobRequest",
    "type": "object",
    "properties": {
        "stages": {
            "type": "array",
            "items": {
                "discriminator": {"propertyName": "task", "mapping": {task: f"#/$defs/{task}" for task in TASKS}}
            },
        }
    },
}
METRICS = {
    "optimize": ({"energy": -137.9, "fmax": 0.01, "converged": True, "steps": 12}, {"energy": "eV", "fmax": "eV/Å"}),
    "frequencies": ({"energy": -137.9, "n_imaginary": 0, "zero_point_energy": 0.56}, {"energy": "eV"}),
}


def model_name(calculator: Any) -> str | None:
    if isinstance(calculator, dict):
        return calculator["model"]
    return None if calculator is None else calculator.partition("@")[0]


def validate(request: dict[str, Any]) -> str | None:
    rows = {row["name"]: row for row in MODELS}
    for calculator in (request.get("calculator"), *(stage.get("calculator") for stage in request["stages"])):
        if (name := model_name(calculator)) is None:
            continue
        if name not in rows:
            return f"ValidationError: 1 validation error for Pipeline\n  Value error, unknown model {name!r}."
        required = rows[name].get("required_options") or {}
        if missing := [key for key in required if key not in (rows[name].get("options") or {})]:
            return (
                "ValidationError: 1 validation error for JobRequest\n  Value error, "
                f"{name} needs the option(s) {', '.join(map(repr, missing))}, "
                f"for example CalculatorSpec({name!r}, options={required!r})."
            )
    for stage in request["stages"]:
        if stage.get("task") not in TASKS:
            return f"ValidationError: 1 validation error for JobRequest\n  unknown task {stage.get('task')!r}"
    return None


def write_manifest(job: Path, status: str, error: dict[str, str] | None, outputs: list[dict[str, Any]]) -> None:
    manifest = {"schema": "atomworks-quantum.job-manifest/1", "status": status, "error": error, "outputs": outputs}
    (job / "outputs").mkdir(exist_ok=True)
    (job / "outputs" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def run_stage(job: Path, index: int, stage: dict[str, Any], model: str | None) -> dict[str, Any]:
    name = f"{index:02d}-{stage['task']}"
    stage_dir = job / "run" / "input" / name
    stage_dir.mkdir(parents=True)
    shutil.copyfile(job / "input.cif", stage_dir / "structure.cif")
    metrics, units = METRICS.get(stage["task"], ({"energy": -137.9}, {"energy": "eV"}))
    (stage_dir / "result.json").write_text(json.dumps(metrics), encoding="utf-8")
    artifacts = {"result": f"run/input/{name}/result.json"}
    if stage["task"] == "optimize":
        (stage_dir / "trajectory.cif").write_text("data_trajectory\n", encoding="utf-8")
        artifacts["trajectory"] = f"run/input/{name}/trajectory.cif"
    return {
        "id": name,
        "label": f"{stage['task']} · {model}",
        "kind": stage["task"],
        "rank": index + 1,
        "structure": f"run/input/{name}/structure.cif",
        "metrics": {**metrics, "charge": 0, "multiplicity": 1},
        "units": units,
        "artifacts": artifacts,
    }


def run(job: Path) -> int:
    request = json.loads((job / "request.json").read_text(encoding="utf-8"))
    mode = os.environ.get("FAKE_AQ_RUN", "completed")
    stages = request["stages"]
    print(f"[aq] job started: {len(stages)} stages, calculator {request.get('calculator')}", flush=True)
    outputs: list[dict[str, Any]] = []
    for index, stage in enumerate(stages):
        name = f"{index:02d}-{stage['task']}"
        print(f"[aq] stage {index + 1}/{len(stages)} {name} running", flush=True)
        if mode == "crash" and index == 1:
            print("Fatal Python error: Segmentation fault", file=sys.stderr, flush=True)
            return 139
        if mode == "failed" and index == 1:
            print(f"[aq] stage {index + 1}/{len(stages)} {name} failed", flush=True)
            error = {"type": "CalculatorUnavailableError", "message": "the ts extra is not installed."}
            write_manifest(job, "failed", error, outputs)
            print("[aq] job failed", flush=True)
            return 3
        if mode == "slow" and index == 1:
            return wait_for_sigterm(job, outputs)
        output = run_stage(job, index, stage, model_name(stage.get("calculator") or request.get("calculator")))
        if mode == "unconverged" and index == len(stages) - 1:
            output["metrics"]["converged"] = False
        if mode == "escape":
            output["structure"] = "../../secret.cif"
            output["artifacts"]["outside"] = "../../secret.json"
        outputs.append(output)
        status = "unconverged" if output["metrics"].get("converged") is False else "done"
        print(f"[aq] stage {index + 1}/{len(stages)} {name} {status}", flush=True)
    if mode == "invalid":
        write_manifest(job, "done", None, outputs)
        return 0
    if mode == "failed-without-error":
        write_manifest(job, "failed", None, outputs)
        print("[aq] job failed", flush=True)
        return 1
    status = "unconverged" if mode == "unconverged" else "completed"
    write_manifest(job, status, None, outputs)
    print(f"[aq] job {status}", flush=True)
    return 0


def wait_for_sigterm(job: Path, outputs: list[dict[str, Any]]) -> int:
    """Stand in for a long stage: a child process in the same group, then a cancelled manifest on SIGTERM."""

    def cancelled(signum: int, frame: object) -> None:
        del frame
        write_manifest(job, "cancelled", {"type": "KeyboardInterrupt", "message": "stopped by SIGTERM."}, outputs)
        print("[aq] job cancelled", flush=True)
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, cancelled)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (job / "pids").write_text(f"{os.getpid()} {child.pid}", encoding="utf-8")
    time.sleep(60)
    return 1


def main(arguments: list[str]) -> int:
    if calls := os.environ.get("FAKE_AQ_CALLS"):
        with open(calls, "a", encoding="utf-8") as handle:
            handle.write(" ".join(arguments) + "\n")
    if os.environ.get("FAKE_AQ_BROKEN") == "1":
        print("Traceback (most recent call last):\nImportError: cannot import name 'tblite'", file=sys.stderr)
        return 1
    match arguments:
        case ["models", "--json", "--installed"]:
            print(json.dumps(MODELS, indent=2))
        case ["job", "schema"]:
            print(json.dumps(SCHEMA, indent=2))
        case ["job", "validate", "-"]:
            if (error := validate(json.loads(sys.stdin.read()))) is not None:
                print(error, file=sys.stderr)
                return 2
        case ["job", "run", directory]:
            return run(Path(directory))
        case _:
            print(f"fake aq: unsupported arguments {arguments}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
