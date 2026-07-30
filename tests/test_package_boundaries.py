"""Static package-boundary checks for migrated Studio code."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (ROOT / "packages", ROOT / "providers")
LEGACY_PLUGIN_NAMESPACES = ("atomworks.pymol_plugin", "rfproteina.pymol")


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_studio_does_not_import_legacy_plugin_namespaces() -> None:
    violations: list[str] = []
    for source_root in SOURCE_ROOTS:
        for path in source_root.rglob("*.py"):
            for module in _imported_modules(path):
                if module.startswith(LEGACY_PLUGIN_NAMESPACES):
                    violations.append(f"{path.relative_to(ROOT)} imports {module}")
    assert not violations, "\n".join(violations)
