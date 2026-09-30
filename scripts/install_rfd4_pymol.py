"""Install and check the local editor inside the environment made by make rfd4."""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    subprocess.run([sys.executable, str(ROOT / "scripts/pin_deps.py")], cwd=ROOT, check=True)
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("The Conda environment is missing uv; recreate it with make rfd4.")
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--python",
            sys.executable,
            "--torch-backend",
            "cpu",
            "numpy<2",
            "scipy<1.17",
            "-e",
            str(ROOT / "external/atomworks-dev"),
            "-e",
            str(ROOT / "packages/editing"),
            "-e",
            str(ROOT / "packages/pymol-plugin"),
            "-e",
            f"{ROOT / 'providers/rfd4'}[pymol,validation]",
        ],
        cwd=ROOT,
        check=True,
    )
    # A new process sees the freshly installed editable-package paths.
    subprocess.run([sys.executable, str(ROOT / "scripts/check_pymol_install.py")], cwd=ROOT, check=True)
    startup_args = []
    if len(sys.argv) > 1 and sys.argv[1]:
        startup_args = [
            "--pymolrc",
            sys.argv[1],
            "--extensions-file",
            str(Path(sys.argv[1]).with_suffix(".extensions")),
        ]
    subprocess.run([sys.executable, "-m", "atomworks_studio_pymol", "install", *startup_args], check=True)


if __name__ == "__main__":
    main()
