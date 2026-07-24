"""AtomWorks PyMOL plugin entry point.

The standalone distribution publishes :func:`__init_plugin__` through the
``pymol.plugins`` entry-point group.  The function remains compatible with
PyMOL's traditional ``run /path/to/atomworks/pymol_plugin/__init__.py`` loader.
"""

import logging
from importlib.util import find_spec

logger = logging.getLogger("atomworks.pymol_plugin")


def __init_plugin__(app: object | None = None) -> None:  # noqa: N807
    """PyMOL plugin entry point.

    Checks that atomworks is importable, then registers the menu item,
    console commands, and command intercepts.

    Args:
        app: PyMOL application object (passed by PyMOL; may be None).
    """
    if find_spec("atomworks") is None:
        _register_unavailable_menu()
        return

    _register_plugin(app)


def _register_unavailable_menu() -> None:
    """Register a placeholder menu item that shows an install error."""
    try:
        from pymol.plugins import addmenuitemqt

        addmenuitemqt("AtomWorks [NOT INSTALLED]", _show_install_error)
    except Exception:
        print(
            "AtomWorks plugin: atomworks is not installed in this Python environment.\n"
            "Reinstall with: python -m pip install atomworks-pymol-plugin"
        )


def _show_install_error() -> None:
    """Show a Qt message box explaining that atomworks is not installed."""
    try:
        from pymol.Qt import QtWidgets

        QtWidgets.QMessageBox.critical(
            None,
            "AtomWorks Not Installed",
            "atomworks is not installed in this Python environment.\n\n"
            "Reinstall it with:\n    python -m pip install atomworks-pymol-plugin\n\n"
            "Ensure you install in the same conda/venv where PyMOL is running.",
        )
    except Exception:
        print(
            "AtomWorks is not installed.\n"
            "Run: python -m pip install atomworks-pymol-plugin "
            "(in the environment where PyMOL runs)"
        )


def _register_plugin(app: object | None) -> None:
    """Register the full plugin: menu, commands, and command intercepts."""
    try:
        from pymol.plugins import addmenuitemqt
    except Exception as e:
        logger.warning("Could not register AtomWorks menu item: %s", e)
        return

    # Register the custom editors for specific annotation classes
    try:
        from atomworks.pymol_plugin.editors.custom import register_custom_editors

        register_custom_editors()
    except Exception as e:
        logger.warning("AtomWorks: could not register custom editors: %s", e)

    # Load user-registered extensions (before commands/intercepts so editors are ready)
    try:
        from atomworks.pymol_plugin.extensions import load_extensions

        load_extensions()
    except Exception as e:
        logger.warning("AtomWorks: could not load pymol extensions: %s", e)

    # Register aw_* console commands
    try:
        from atomworks.pymol_plugin.commands import register_commands

        register_commands()
    except Exception as e:
        logger.warning("AtomWorks: could not register console commands: %s", e)

    # Add the menu item — must tolerate headless environments (e.g. pytest)
    # where Qt isn't available even though ``pymol`` itself imports fine.
    try:
        from atomworks.pymol_plugin.panel import open_main_panel

        addmenuitemqt("AtomWorks", open_main_panel)
    except Exception as e:
        logger.warning("AtomWorks: could not register Qt menu item: %s", e)
        return
    logger.info("AtomWorks PyMOL plugin loaded successfully.")
    print("AtomWorks plugin loaded. Use Plugin → AtomWorks or type aw_load in the console.")


# When loaded via `run __init__.py` in ~/.pymolrc, PyMOL executes this file
if __name__ != "atomworks.pymol_plugin":
    __init_plugin__()
