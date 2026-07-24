"""PyMOL plugin extension management.

Extensions are Python modules with a ``register()`` function that hook
custom annotation editors into the AtomWorks PyMOL plugin.

The list of active extensions is stored in ``~/.atomworks/pymol_extensions``,
one module path per line.  Manage via the CLI::

    atomworks pymol add proteinfoundation.pymol.editors
    atomworks pymol remove proteinfoundation.pymol.editors
    atomworks pymol list
"""

import importlib
import logging
from pathlib import Path

logger = logging.getLogger("atomworks.pymol_plugin")

_EXTENSIONS_FILE = Path.home() / ".atomworks" / "pymol_extensions"


def extensions_file() -> Path:
    """Return the path to the extensions config file."""
    return _EXTENSIONS_FILE


def read_extensions() -> list[str]:
    """Return the list of registered extension module paths."""
    if not _EXTENSIONS_FILE.exists():
        return []
    lines = _EXTENSIONS_FILE.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def add_extension(module: str) -> bool:
    """Append *module* to the extensions file.

    Args:
        module: Dotted Python module path, e.g. ``proteinfoundation.pymol.editors``.

    Returns:
        True if added, False if already present.
    """
    _EXTENSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if module in read_extensions():
        return False
    with _EXTENSIONS_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{module}\n")
    return True


def remove_extension(module: str) -> bool:
    """Remove *module* from the extensions file.

    Args:
        module: Dotted Python module path to remove.

    Returns:
        True if removed, False if it was not present.
    """
    if not _EXTENSIONS_FILE.exists():
        return False
    lines = _EXTENSIONS_FILE.read_text(encoding="utf-8").splitlines()
    new_lines = [ln for ln in lines if ln.strip() != module]
    if len(new_lines) == len(lines):
        return False
    text = "\n".join(new_lines)
    if text:
        text += "\n"
    _EXTENSIONS_FILE.write_text(text, encoding="utf-8")
    return True


def load_extensions() -> None:
    """Import each registered extension module and call its ``register()`` function.

    Errors in individual extensions are logged as warnings and do not prevent
    other extensions from loading.
    """
    for module_path in read_extensions():
        try:
            mod = importlib.import_module(module_path)
            mod.register()
            logger.info("AtomWorks: loaded extension '%s'", module_path)
        except Exception as e:
            logger.warning("AtomWorks: failed to load extension '%s': %s", module_path, e)
