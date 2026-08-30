"""Register RFD4 PyMOL presentation from the shared annotation contract."""

from __future__ import annotations

import functools
from collections.abc import Callable
from enum import IntEnum

import numpy as np
from annotation_studio.contracts import ConditionDefinition
from atomworks.io.utils.atom_array_plus import AtomArrayPlus
from atomworks.io.utils.standard_annotations.base import STANDARD_ANNOTATIONS
from atomworks.ml.conditions import C_DIS, C_IDX
from atomworks.ml.conditions.base import ConditionBase
from atomworks.ml.utils.visualize import display_atom_ids
from atomworks_studio_pymol.editors import editor_for, exclude_annotation, register_editor
from atomworks_studio_pymol.editors.widgets import BaseAnnotationEditor, MaskEditor, PairValueEditor
from atomworks_studio_pymol.panel import register_summary_hook
from atomworks_studio_pymol.views import register_selection_generator
from rfproteina.conditions import C_CENTROID, C_CRDGRP, C_IDXGRP, C_INTCRD
from rfproteina.conditions_reporting import summarize_conditions, sync_index_groups

from atomworks_studio_rfd4.condition_contract import CONDITION_DEFINITIONS, enum_type
from atomworks_studio_rfd4.pymol.chain_centroid_editor import ChainCentroidEditor
from atomworks_studio_rfd4.pymol.group_editor import GroupEditor
from atomworks_studio_rfd4.pymol.internal_coord_editor import InternalCoordEditor
from atomworks_studio_rfd4.pymol.widgets import ValueButtonEditor


class _IndexMaskEditor(MaskEditor):
    """Mask editor for Index that keeps C_IDXGRP synchronized."""

    def _set_mask(self, value: bool) -> bool:
        updated = super()._set_mask(value)
        if updated:
            self._sync_index_groups(preserve_manual_edits=True)
        return updated

    def _reset(self) -> None:
        super()._reset()
        self._sync_index_groups(preserve_manual_edits=False)

    def _sync_index_groups(self, preserve_manual_edits: bool = True) -> None:
        obj_name = self._state.active
        if not obj_name or obj_name not in self._state.arrays:
            return
        array = self._state.get_synced_array(obj_name)
        sync_index_groups(array, preserve_manual_edits)
        self._reload_or_recolor()


def _enum_editor(enum_cls: type[IntEnum]) -> Callable[..., ValueButtonEditor]:
    null_value = next(iter(enum_cls)).value
    return functools.partial(
        ValueButtonEditor,
        named_values=[(member.name, member.value) for member in enum_cls if member.name != "NON_PROTEIN"],
        null_value=null_value,
    )


@functools.cache
def _make_enum_selection_generator(
    condition_cls: type[ConditionBase], enum_cls: type[IntEnum]
) -> Callable[[AtomArrayPlus, str], None]:
    prefix = condition_cls.name.lower()
    null_value = next(iter(enum_cls)).value

    def _generator(array: AtomArrayPlus, obj_name: str) -> None:
        from pymol import cmd

        for enum_value in enum_cls:
            if enum_value.value != null_value:
                cmd.delete(f"{prefix}_{enum_value.name.lower()}_{obj_name}")
        if not condition_cls.has_annotation(array):
            return
        annotation = condition_cls.annotation(array, default="raise")
        mask = condition_cls.mask(array, default="generate")
        atom_ids = display_atom_ids(array)
        for enum_value in enum_cls:
            atom_mask = mask & (annotation == enum_value.value)
            if enum_value.value == null_value or not atom_mask.any():
                continue
            ids = atom_ids[np.flatnonzero(atom_mask)]
            cmd.select(
                f"{prefix}_{enum_value.name.lower()}_{obj_name}",
                f"model {obj_name} and id {'+'.join(str(int(value)) for value in ids)}",
                enable=0,
            )

    _generator.__name__ = f"_selection_generator_{prefix}"
    return _generator


_CUSTOM_EDITORS: dict[type[ConditionBase], Callable[..., BaseAnnotationEditor]] = {
    C_IDX: _IndexMaskEditor,
    C_IDXGRP: GroupEditor,
    C_CRDGRP: GroupEditor,
    C_INTCRD: InternalCoordEditor,
    C_CENTROID: ChainCentroidEditor,
    C_DIS: PairValueEditor,
}


def _custom_editor(definition: ConditionDefinition) -> Callable[..., BaseAnnotationEditor] | None:
    condition = definition.condition
    if (declared_enum := enum_type(condition)) is not None:
        return _enum_editor(declared_enum)
    return _CUSTOM_EDITORS.get(condition)


def apply() -> None:
    """Register RFD4 editors and hide annotations unsupported by this UI."""
    from atomworks.ml.utils.visualize import hide_condition_display

    for definition in CONDITION_DEFINITIONS:
        condition = definition.condition
        if (editor := _custom_editor(definition)) is not None:
            register_editor(condition)(editor)
        if condition in {C_IDXGRP, C_CRDGRP}:
            hide_condition_display(condition)
        if (declared_enum := enum_type(condition)) is not None:
            register_selection_generator(_make_enum_selection_generator(condition, declared_enum))

    listed_conditions = {definition.condition for definition in CONDITION_DEFINITIONS}
    for annotation_cls in STANDARD_ANNOTATIONS:
        supported = annotation_cls in listed_conditions
        if not issubclass(annotation_cls, ConditionBase) and annotation_cls.editing_type is not None:
            try:
                editor_for(annotation_cls)
                supported = True
            except TypeError:
                pass
        if not supported:
            exclude_annotation(annotation_cls)
            if issubclass(annotation_cls, ConditionBase):
                hide_condition_display(annotation_cls)

    register_summary_hook(summarize_conditions)
