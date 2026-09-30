"""Exercise fresh installs and native dependency repairs without changing Conda."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("use_prefix", [False, True])
@pytest.mark.parametrize("existing, update_status", [(False, 0), (True, 0), (True, 1)])
def test_setup_applies_native_dependencies_before_installing(
    tmp_path: Path, use_prefix: bool, existing: bool, update_status: int
) -> None:
    log = tmp_path / "conda.jsonl"
    conda = tmp_path / "conda"
    conda.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "with Path(os.environ['CONDA_TEST_LOG']).open('a') as stream:\n"
        "    stream.write(json.dumps(args) + '\\n')\n"
        "if args[0] == 'run' and '-c' in args:\n"
        "    sys.exit(0 if os.environ['CONDA_TEST_EXISTS'] == '1' else 1)\n"
        "if args[:2] == ['env', 'update']:\n"
        "    sys.exit(int(os.environ['CONDA_TEST_UPDATE_STATUS']))\n"
    )
    conda.chmod(0o755)
    environment = str(tmp_path / "env with spaces") if use_prefix else "test-rfd4"
    target = ["-p" if use_prefix else "-n", environment]
    startup = str(tmp_path / "startup with spaces")
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/setup_rfd4_pymol.sh"), environment, startup],
        cwd=ROOT,
        env={
            **os.environ,
            "CONDA_EXE": str(conda),
            "CONDA_TEST_LOG": str(log),
            "CONDA_TEST_EXISTS": str(int(existing)),
            "CONDA_TEST_UPDATE_STATUS": str(update_status),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    operation = ["env", "update" if existing else "create", *target, "-f", "packages/pymol-plugin/rfd4_env.yaml"]
    if not existing:
        operation.append("--yes")
    assert calls[1] == operation
    assert result.returncode == update_status, result.stderr
    if update_status:
        assert len(calls) == 2
        assert "Ready." not in result.stdout
    else:
        assert calls[2:] == [
            ["run", "--no-capture-output", *target, "python", "scripts/install_rfd4_pymol.py", startup]
        ]
        assert "Ready." in result.stdout
