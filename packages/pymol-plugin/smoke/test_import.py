"""Installation smoke tests that do not require a running PyMOL process."""

from importlib.metadata import entry_points

import atomworks.pymol_plugin as plugin


def test_plugin_entry_point_is_importable() -> None:
    assert callable(plugin.__init_plugin__)


def test_pymol_entry_point_targets_plugin_initializer() -> None:
    matching = [
        entry_point
        for entry_point in entry_points(group="pymol.plugins")
        if entry_point.name == "atomworks"
        and entry_point.value == "atomworks.pymol_plugin:__init_plugin__"
    ]
    assert matching
    assert matching[0].load() is plugin.__init_plugin__
