"""Import-time setup so the CPU editor can load RFD4 conditions without its heavy init."""

from __future__ import annotations

import importlib.util
import sys
import types


def install_lightweight_rfproteina_stub() -> None:
    """Let the CPU editor import ``rfproteina.conditions`` without RFD4's heavy package init.

    RFD4 ``main``'s ``rfproteina/__init__.py`` runs a heavy init (``environs``/``rootutils``
    and the datapipes stack) unconditionally, but Studio only needs ``rfproteina.conditions``.
    Registering a lightweight package stub (pointing at the real source tree) makes
    ``rfproteina.*`` submodule imports resolve while skipping that package init, so Studio
    can consume the pinned RFD4 modules without running its package initializer. (``torch`` still
    loads transitively via ``atomworks.ml`` — the stub only avoids the RFD4-specific init.)
    No-op if ``rfproteina`` was already imported or is not on the path.
    """
    if "rfproteina" in sys.modules:
        return
    spec = importlib.util.find_spec("rfproteina")
    if spec is None or not spec.submodule_search_locations:
        return
    stub = types.ModuleType("rfproteina")
    stub.__path__ = list(spec.submodule_search_locations)
    stub.__spec__ = spec
    sys.modules["rfproteina"] = stub
