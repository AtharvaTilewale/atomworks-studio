from __future__ import annotations

import json
import os
import subprocess
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]


class QuietFileHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def make_rfd4_fixture(tmp_path: Path) -> tuple[Path, Path]:
    rfd4_root = tmp_path / "rfd4"
    data_path = tmp_path / "data"
    (rfd4_root / "configs").mkdir(parents=True)
    (rfd4_root / "configs/inference.yaml").write_text(
        "\n".join(
            (
                "ckpt_name: release.ckpt",
                "ckpt_path: ${oc.env:DATA_PATH}/latent_checkpoints/ldm_checkpoints",
                "autoencoder_ckpt_path: ${oc.env:DATA_PATH}/latent_checkpoints/ae_checkpoints/ae.ckpt",
            )
        ),
        encoding="utf-8",
    )
    for relative_path in (
        "latent_checkpoints/ldm_checkpoints/release.ckpt",
        "latent_checkpoints/ae_checkpoints/ae.ckpt",
    ):
        path = data_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"checkpoint")
    return rfd4_root, data_path


def test_asset_check_cli_verifies_downloaded_files_and_writes_evidence(tmp_path: Path) -> None:
    rfd4_root, data_path = make_rfd4_fixture(tmp_path)
    report_path = tmp_path / "asset-report.json"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(QuietFileHandler, directory=str(data_path)),
    )
    server_thread = Thread(target=server.serve_forever)
    server_thread.start()
    try:
        completed = subprocess.run(
            (
                sys.executable,
                str(ROOT / "scripts/check_rfd4_assets.py"),
                "--rfd4-root",
                str(rfd4_root),
                "--data-path",
                str(data_path),
                "--check-bucket",
                "--json",
                str(report_path),
            ),
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "RFPROTEINA_DATA_URL": f"http://127.0.0.1:{server.server_port}",
            },
        )
    finally:
        server.shutdown()
        server_thread.join()

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    assert [asset["local_bytes"] for asset in report["assets"]] == [10, 10]
    assert [asset["bucket_bytes"] for asset in report["assets"]] == [10, 10]
    assert [asset["path"] for asset in report["assets"]] == [
        "latent_checkpoints/ldm_checkpoints/release.ckpt",
        "latent_checkpoints/ae_checkpoints/ae.ckpt",
    ]


def test_asset_check_cli_rejects_a_stale_data_path(tmp_path: Path) -> None:
    rfd4_root, data_path = make_rfd4_fixture(tmp_path)
    (data_path / "latent_checkpoints/ldm_checkpoints/release.ckpt").unlink()

    completed = subprocess.run(
        (
            sys.executable,
            str(ROOT / "scripts/check_rfd4_assets.py"),
            "--rfd4-root",
            str(rfd4_root),
            "--data-path",
            str(data_path),
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "scripts/download_example_data.sh" in completed.stderr
