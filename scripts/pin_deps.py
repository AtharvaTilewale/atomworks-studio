"""Check dependency pins that define a reproducible Studio distribution."""

from __future__ import annotations

import json
import re
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
    api = tomllib.loads((ROOT / "packages/api/pyproject.toml").read_text())
    dependencies = api["project"]["dependencies"]
    assert any(item.startswith("atomworks") for item in dependencies)
    assert all("../../../" not in item for item in dependencies)
    print(
        json.dumps(
            {
                "atomworks": next(item for item in dependencies if item.startswith("atomworks")),
                "molstar": web["dependencies"]["molstar"],
                "patinae_revision": patinae["revision"],
                "patinae_patches": len(patches),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
