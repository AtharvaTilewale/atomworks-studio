from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from annotation_studio.design_jobs import (
    AF3_ENVIRONMENT_KEYS,
    DesignJobRequest,
    ProcessDesignProvider,
    SearchOptions,
    build_rfd4_config,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "data" / "benchmarks" / "SAM_hbonds.cif"
FAKE_RFD4 = Path(__file__).parent / "fake_rfd4.py"


def test_schema_only_advertises_af3_metrics_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for key in (*AF3_ENVIRONMENT_KEYS, "AF3_LAUNCHER_SOCKET_TEMPLATE"):
        monkeypatch.delenv(key, raising=False)
    manager = ProcessDesignProvider(root=tmp_path / "jobs", project_root=tmp_path, command=["rfd4"])

    local_schema = manager.schema()
    assert local_schema["capabilities"]["af3"] is False
    assert "design" in local_schema["metrics_tags"]
    assert "unindexed" in local_schema["metrics_tags"]
    assert "unconditional_refolding" not in local_schema["metrics_tags"]
    assert local_schema["defaults"]["metrics_tags"] == ["design"]
    assert local_schema["defaults"]["search"] == {
        "step_checkpoints": [0, 50, 100],
        "n_branch": 2,
        "beam_width": 2,
        "reward_metrics_tags": ["interchain_interactions"],
        "weights": {"interchain_interactions.hbond_atom_pair_count_coi": 1.0},
    }

    for key in AF3_ENVIRONMENT_KEYS:
        monkeypatch.setenv(key, "/configured")
    af3_schema = manager.schema()
    assert af3_schema["capabilities"]["af3"] is True
    assert {
        "ppi_refolding",
        "ame_refolding",
        "disco_refolding",
        "sm_binder_refolding",
        "unconditional_refolding",
    } <= set(af3_schema["metrics_tags"])
    manager.close()


def test_build_search_config_keeps_io_server_controlled(tmp_path: Path) -> None:
    request = DesignJobRequest(
        mode="search",
        num_designs=4,
        steps=100,
        search=SearchOptions(
            step_checkpoints=[0, 50, 100],
            weights={"oracle.plddt_coi": 1.0},
            reward_metrics_tags=["unconditional_refolding"],
        ),
        config_yaml="batch_size: 2\ngeneration:\n  args:\n    self_cond: true\n",
    )
    config = build_rfd4_config(
        request,
        input_path=tmp_path / "input.cif",
        output_dir=tmp_path / "outputs",
        run_name="test-job",
        project_root=tmp_path,
    )
    assert config["inputs"] == str(tmp_path / "input.cif")
    assert config["batch_size"] == 2
    assert config["generation"]["args"] == {"nsteps": 100, "self_cond": True}
    assert config["search"]["strategy"]["_target_"].endswith("BeamSearchStrategy")
    assert config["search"]["rewards"]["weights"] == {"oracle.plddt_coi": 1.0}


@pytest.mark.parametrize(
    "config_yaml",
    [
        "inputs: /etc/passwd",
        "generation:\n  model:\n    _target_: os.system",
        "generation:\n  value: ${oc.env:SECRET}",
        "num_replicates: 1000000",
    ],
)
def test_model_options_reject_unsafe_or_unbounded_values(tmp_path: Path, config_yaml: str) -> None:
    with pytest.raises(ValueError):
        build_rfd4_config(
            DesignJobRequest(config_yaml=config_yaml),
            input_path=tmp_path / "input.cif",
            output_dir=tmp_path / "outputs",
            run_name="test-job",
            project_root=tmp_path,
        )


def test_local_gpu_provider_collects_cifs_and_metrics(tmp_path: Path) -> None:
    manager = ProcessDesignProvider(
        root=tmp_path / "jobs",
        project_root=tmp_path,
        command=[sys.executable, str(FAKE_RFD4)],
        timeout_seconds=30,
    )
    submitted = manager.submit(FIXTURE.read_text(encoding="utf-8"), "fixture", DesignJobRequest(num_designs=1))
    deadline = time.monotonic() + 10
    while (record := manager.get(submitted["id"]))["status"] not in {"completed", "failed"}:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    manager.close()

    assert record["status"] == "completed", record["log_tail"]
    assert len(record["outputs"]) == 1
    assert record["outputs"][0]["rank"] == 1
    assert record["outputs"][0]["metrics"]["oracle.plddt_coi"] == 91.2
    cif, output = manager.output_cif(record["id"], record["outputs"][0]["id"])
    assert "_atom_site." in cif
    assert output["kind"] == "generated"
