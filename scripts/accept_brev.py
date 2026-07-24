"""End-to-end API, RFD4, comparison-data, and AF3 acceptance."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = os.environ.get("ANNOTATION_STUDIO_URL", "http://127.0.0.1:8000").rstrip("/")
ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = Path(os.environ["ATOMWORKS_STUDIO_ACCEPTANCE_ARTIFACT"])


def request(path: str, *, method: str = "GET", payload: dict | None = None) -> bytes:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    try:
        with urlopen(Request(BASE + path, data=body, headers=headers, method=method), timeout=120) as response:
            return response.read()
    except HTTPError as error:
        raise RuntimeError(f"{method} {path}: {error.code} {error.read().decode()}") from error


def json_request(path: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    return json.loads(request(path, method=method, payload=payload))


def upload(path: Path, *, name: str | None = None) -> dict:
    boundary = "----atomworks-studio-acceptance"
    body = b"".join(
        (
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{name or path.name}"\r\n'.encode(),
            b"Content-Type: chemical/x-mmcif\r\n\r\n",
            path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        )
    )
    with urlopen(
        Request(
            BASE + "/api/sessions",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        ),
        timeout=120,
    ) as response:
        return json.loads(response.read())


def await_job(job_id: str, timeout: float = 45 * 60) -> dict:
    deadline = time.monotonic() + timeout
    phases: list[str] = []
    while time.monotonic() < deadline:
        job = json_request(f"/api/design-jobs/{job_id}")
        phase = job.get("progress", {}).get("phase")
        if phase and phase not in phases:
            phases.append(phase)
        if job["status"] == "failed":
            raise RuntimeError(f"{job.get('error')}\n{job.get('log_tail', '')}")
        if job["status"] == "completed":
            job["observed_phases"] = phases
            return job
        time.sleep(5)
    raise TimeoutError(job_id)


health = json_request("/api/health")
ready = json_request("/api/ready")
assert health["design_jobs"] == "enabled", health
assert ready["status"] == "ready", ready

fixture = ROOT / "tests/fixtures/SAM_hbonds.cif"
created = upload(fixture)
session = created["session_id"]
selection = json_request(
    f"/api/sessions/{session}/selections/query",
    method="POST",
    payload={"expression": "chain_id == 'A'", "syntax": "atomworks", "scope": "atom"},
)
assert selection["atom_indices"]
json_request(f"/api/sessions/{session}/charges", method="POST", payload={"atom_indices": [0], "delta": 1})
json_request(f"/api/sessions/{session}/chains", method="POST", payload={"atom_indices": [0], "chain_id": "WEB"})
json_request(
    f"/api/sessions/{session}/distance-restraints",
    method="POST",
    payload={"atom1": 0, "atom2": 1, "action": "set", "distance": 4.0},
)
interactions = json_request(
    f"/api/sessions/{session}/interactions/analyze",
    method="POST",
    payload={"interaction_types": ["hbond"]},
)
assert "interactions" in interactions

roundtrip_path = ARTIFACT.parent / "roundtrip.cif"
roundtrip_path.write_bytes(request(f"/api/sessions/{session}/cif"))
roundtrip = upload(roundtrip_path)
assert roundtrip["structure"]["atom_count"] == created["structure"]["atom_count"]

direct = json_request(
    f"/api/sessions/{session}/design-jobs",
    method="POST",
    payload={"mode": "generate", "num_designs": 1, "batch_size": 1, "steps": 1, "seed": 4101},
)
direct_job = await_job(direct["id"])
assert direct_job["outputs"]
for output in direct_job["outputs"][:2]:
    opened = json_request(f"/api/design-jobs/{direct['id']}/outputs/{output['id']}/open", method="POST")
    assert opened["session_id"]

af3_job = None
if os.environ.get("ATOMWORKS_STUDIO_ACCEPT_AF3", "1") == "1":
    submitted = json_request(
        f"/api/sessions/{session}/design-jobs",
        method="POST",
        payload={
            "mode": "search",
            "num_designs": 1,
            "batch_size": 1,
            "steps": 1,
            "seed": 4102,
            "metrics_tags": ["design", "unconditional_refolding"],
            "search": {
                "step_checkpoints": [0, 1],
                "n_branch": 1,
                "beam_width": 1,
                "reward_metrics_tags": ["unconditional_refolding"],
                "weights": {"oracle.ranking_score": 1.0},
            },
        },
    )
    af3_job = await_job(submitted["id"])
    metrics = [output["metrics"] for output in af3_job["outputs"]]
    assert any(values.get("oracle.ranking_score") is not None for values in metrics), metrics

ARTIFACT.write_text(
    json.dumps(
        {
            "health": health,
            "ready": ready,
            "selection_count": len(selection["atom_indices"]),
            "interactions": len(interactions["interactions"]),
            "direct_job": direct_job,
            "af3_job": af3_job,
        },
        indent=2,
        sort_keys=True,
    ),
    encoding="utf-8",
)
print(f"Brev acceptance passed; artifact: {ARTIFACT}")
