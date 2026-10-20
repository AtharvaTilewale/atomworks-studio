"""Bundle the already-built browser application into Python distributions."""

from __future__ import annotations

import hashlib
import shutil
import tomllib
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Stage Vite output and verify the pinned RFD4 definitions before each build."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        self._bundle_rfd4_definitions(build_data)
        if version == "editable":
            return
        for source_dir, name in (("packages/web/dist", "web"), ("docs/_build/html", "guide")):
            source = Path(self.root) / source_dir
            destination = Path(self.root) / "packages/api/annotation_studio" / name
            if (source / "index.html").is_file():
                shutil.rmtree(destination, ignore_errors=True)
                shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".doctrees", "__pycache__", "*.bak"))
            elif not (destination / "index.html").is_file():
                raise RuntimeError(f"build {source_dir} before building Studio (make build)")
            # Also include generated assets when rebuilding a wheel from its sdist.
            prefix = "packages/api/" if self.target_name == "sdist" else ""
            build_data.setdefault("force_include", {})[str(destination)] = f"{prefix}annotation_studio/{name}"

    def _bundle_rfd4_definitions(self, build_data: dict) -> None:
        """Verify every pinned definition file and ship its manifest beside them."""
        provider = Path(self.root) / "providers/rfd4"
        source = provider / "atomworks_studio_rfd4/_upstream"
        manifest = provider / "UPSTREAM.toml"
        metadata = tomllib.loads(manifest.read_text())
        for name, expected in metadata["files"].items():
            path = source / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeError(f"RFD4 {name} does not match pinned RosettaCommons revision {metadata['revision']}")
        if self.target_name == "wheel":
            build_data.setdefault("force_include", {})[str(manifest)] = "atomworks_studio_rfd4/_upstream/UPSTREAM.toml"
