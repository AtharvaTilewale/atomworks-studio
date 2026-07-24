"""Validate the shared website/extension release version."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

STUDIO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default=None)
    parser.add_argument("--github-output", action="store_true")
    arguments = parser.parse_args()

    version = (STUDIO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if (
        re.fullmatch(
            r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?",
            version,
        )
        is None
    ):
        raise SystemExit(f"invalid Annotation Studio version: {version!r}")

    manifests = {
        "workspace": STUDIO_ROOT / "package.json",
        "website": STUDIO_ROOT / "packages" / "web" / "package.json",
        "VS Code extension": STUDIO_ROOT / "packages" / "vscode" / "package.json",
    }
    for label, path in manifests.items():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("version") != version:
            raise SystemExit(f"{label} version {manifest.get('version')!r} does not match VERSION {version!r}")
    api_manifest = (STUDIO_ROOT / "packages" / "api" / "pyproject.toml").read_text(encoding="utf-8")
    project_section = api_manifest.split("[project]", 1)[-1].split("\n[", 1)[0]
    api_version_match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', project_section, re.MULTILINE)
    api_version = api_version_match.group(1) if api_version_match else None
    if api_version != version:
        raise SystemExit(f"Python API version {api_version!r} does not match VERSION {version!r}")

    tag = arguments.tag or os.environ.get("GITHUB_REF_NAME", "")
    if tag and tag.startswith("v") and tag != f"v{version}":
        raise SystemExit(f"release tag {tag!r} must equal v{version}")

    if arguments.github_output:
        output = os.environ.get("GITHUB_OUTPUT")
        if not output:
            raise SystemExit("GITHUB_OUTPUT is required with --github-output")
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"version={version}\n")
    print(version)


if __name__ == "__main__":
    main()
