"""Check dependency pins that define a reproducible Studio distribution."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    web = json.loads((ROOT / "packages/web/package.json").read_text())
    assert web["dependencies"]["molstar"] == "5.11.0", "Mol* must be exact-pinned"
    patinae = tomllib.loads((ROOT / "vendor/patinae/UPSTREAM.toml").read_text())
    assert re.fullmatch(r"[0-9a-f]{40}", patinae["revision"])
    patches = sorted((ROOT / "vendor/patinae/patches").glob("*.patch"))
    assert patches, "Patinae patch series is empty"

    # atomworks and RFD4 are git submodules pinned by commit in dependencies.toml; every
    # submodule must declare a 40-hex revision under external/ so the pin is reproducible.
    manifest = tomllib.loads((ROOT / "dependencies.toml").read_text())
    atomworks_requirement = f"atomworks[ml]>={manifest['atomworks']['version']},<3"
    dependency_manifests = (
        ROOT / "packages/api/pyproject.toml",
        ROOT / "packages/editing/pyproject.toml",
        ROOT / "packages/pymol-plugin/pyproject.toml",
    )
    for path in dependency_manifests:
        dependencies = tomllib.loads(path.read_text())["project"]["dependencies"]
        assert atomworks_requirement in dependencies, (
            f"{path.relative_to(ROOT)} must require the reviewed AtomWorks major range"
        )
        assert all("../../../" not in item for item in dependencies)

    revisions = {}
    for name in ("atomworks", "rfd4"):
        entry = manifest[name]
        assert re.fullmatch(r"[0-9a-f]{40}", entry["revision"]), f"{name} revision must be a 40-hex commit"
        assert entry["submodule"].startswith("external/"), f"{name} must be an external/ submodule"
        gitlink = subprocess.check_output(("git", "rev-parse", f":{entry['submodule']}"), cwd=ROOT, text=True).strip()
        assert entry["revision"] == gitlink, f"{name} manifest revision does not match its submodule gitlink"
        revisions[f"{name}_revision"] = entry["revision"]

    rfd4_atomworks = subprocess.check_output(
        ("git", "-C", manifest["rfd4"]["submodule"], "rev-parse", ":lib/atomworks"), cwd=ROOT, text=True
    ).strip()
    assert rfd4_atomworks == revisions["atomworks_revision"], "RFD4 and Studio must pin the same AtomWorks revision"

    print(
        json.dumps(
            {
                "atomworks": atomworks_requirement,
                "molstar": web["dependencies"]["molstar"],
                "patinae_revision": patinae["revision"],
                "patinae_patches": len(patches),
                **revisions,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
