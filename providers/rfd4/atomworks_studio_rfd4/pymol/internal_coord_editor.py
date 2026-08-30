"""Annotation Wizard editor for mask-authored internal coordinates."""

import logging

import numpy as np
from atomworks.io.utils.standard_annotations.base import StandardAnnotationBase
from atomworks_studio_pymol.editors.widgets import BaseAnnotationEditor
from atomworks_studio_pymol.state import PluginState
from pymol.Qt import QtWidgets

from atomworks_studio_rfd4.internal_coordinate import set_internal_coordinate_conditioning

logger = logging.getLogger("atomworks_studio_pymol")


class InternalCoordEditor(QtWidgets.QWidget, BaseAnnotationEditor):
    """Mask/group editor that leaves coordinate values to featurization."""

    def __init__(
        self,
        annotation_cls: type[StandardAnnotationBase],
        state: PluginState,
        parent: QtWidgets.QWidget | None = None,
    ):
        super().__init__(parent)
        self._annotation_cls = annotation_cls
        self._state = state

        layout = QtWidgets.QVBoxLayout(self)

        header = QtWidgets.QLabel(f"<b>{annotation_cls.name}</b>")
        layout.addWidget(header)

        desc = QtWidgets.QLabel(
            "Mark the selected atoms for internal-coordinate conditioning. "
            "The selection gets its own coordinate group and its absolute-coordinate "
            "mask is cleared; RFD4 derives the values during featurization."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(8)

        mark_button = QtWidgets.QPushButton("Mark selection as internal coords")
        unset_button = QtWidgets.QPushButton("Unset internal-coord mask on selection")
        layout.addWidget(mark_button)
        layout.addWidget(unset_button)

        layout.addSpacing(6)
        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        layout.addStretch()

        mark_button.clicked.connect(self._mark)
        unset_button.clicked.connect(lambda: self._update(False))

    def _mark(self) -> None:
        self._update(True)

    def _update(self, enabled: bool) -> None:
        obj_name = self._state.active
        if not obj_name or obj_name not in self._state.arrays:
            return
        indices = self._get_selection_indices()
        if not indices:
            QtWidgets.QMessageBox.warning(self, "No selection", "Make a selection in PyMOL first.")
            return

        arr = self._state.get_synced_array(obj_name)

        selected = np.asarray(indices)
        affected = selected
        if enabled:
            affected = selected[np.isfinite(arr.coord[selected]).all(axis=1)]
            if len(affected) == 0:
                QtWidgets.QMessageBox.warning(
                    self, "No resolved atoms", "None of the selected atoms have resolved coordinates."
                )
                return

        self._state.push_snapshot(obj_name)
        try:
            set_internal_coordinate_conditioning(arr, affected, enabled)
        except Exception as error:
            self._state.pop_snapshot(obj_name)
            QtWidgets.QMessageBox.critical(self, "Update failed", str(error))
            return

        if enabled:
            status = f"Marked {len(affected)} atoms for internal coordinates."
            n_skipped = len(selected) - len(affected)
            if n_skipped:
                status += f" ({n_skipped} unresolved atom(s) in the selection were skipped.)"
        else:
            status = f"Unset internal coordinates on {len(affected)} atoms."
        self._status.setText(status)
        self._reload_or_recolor()

    def apply_to_selection(self) -> None:
        self._mark()
