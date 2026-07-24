"""Bundle the already-built browser application into Python distributions."""

from __future__ import annotations

import shutil
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Stage Vite output inside the importable package before each build."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        del build_data
        if version == "editable":
            return
        source = Path(self.root).parent.joinpath("web", "dist")
        destination = Path(self.root) / "annotation_studio" / "web"
        if not (source / "index.html").is_file():
            if (destination / "index.html").is_file():
                return
            raise RuntimeError("build packages/web before building the Studio API package")
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(source, destination)
