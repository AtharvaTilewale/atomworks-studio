"""Bundle the already-built browser application into Python distributions."""

from __future__ import annotations

import shutil
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Stage Vite output inside the importable package before each build."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        del version, build_data
        source = Path(self.root).parent / "web" / "dist"
        if not (source / "index.html").is_file():
            raise RuntimeError("build apps/annotation-studio/web before building the Studio Python package")
        destination = Path(self.root) / "annotation_studio" / "web"
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(source, destination)
