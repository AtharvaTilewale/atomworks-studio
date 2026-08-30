"""Adapt the shared StandardAnnotation editing contract to Studio's UI schema.

The annotation classes own semantic authoring metadata (value type, mask-only
behavior, aliases, level, and body order).  This module contains only Studio
presentation policy such as labels, count units, and the few custom widgets.
"""

from __future__ import annotations

import re
from enum import IntEnum
from types import MappingProxyType

import numpy as np
from annotation_studio.contracts import ConditionDefault, ConditionDefinition, ConditionKind, ConditionValue, CountUnit
from atomworks.io.utils.standard_annotations.base import STANDARD_ANNOTATIONS, Level
from atomworks.ml.conditions import C_CRD, C_DIS, C_IDX, C_SEQ
from atomworks.ml.conditions.base import ConditionBase
from rfproteina.conditions import C_CENTROID, C_CRDGRP, C_IDXGRP, C_INTCRD, C_RAS

_KEY_OVERRIDES: dict[type[ConditionBase], str] = {C_RAS: "rasa"}
_LABEL_OVERRIDES: dict[type[ConditionBase], str] = {
    C_SEQ: "Sequence conditioning",
    C_CRD: "Coordinate conditioning",
    C_IDX: "Index conditioning",
    C_RAS: "RASA",
    C_INTCRD: "Internal coordinate",
    C_CENTROID: "Chain centroid",
    C_DIS: "Distance restraints",
}
_COUNT_UNIT_OVERRIDES: dict[type[ConditionBase], CountUnit] = {
    C_IDXGRP: "groups",
    C_CRDGRP: "groups",
    C_CENTROID: "chains",
    C_DIS: "pairs",
}
_DEFAULT_OVERRIDES: dict[type[ConditionBase], ConditionDefault] = {
    C_IDX: True,
    C_IDXGRP: -1,
    C_CRDGRP: -1,
}


def editable_condition_classes() -> tuple[type[ConditionBase], ...]:
    """Return conditions supported by Studio's current generic editors.

    The value semantics still come entirely from the StandardAnnotation
    contract. Studio adds one capability boundary: its pair editor currently
    supports distance restraints only, and writable strings need a dedicated
    text editor before they can be exposed safely.
    """
    return tuple(
        annotation
        for annotation in STANDARD_ANNOTATIONS
        if issubclass(annotation, ConditionBase) and _supports_studio_editor(annotation)
    )


def _supports_studio_editor(condition: type[ConditionBase]) -> bool:
    if condition.editing_type is None:
        return False
    if condition.n_body != 1:
        return condition is C_DIS
    try:
        condition_kind(condition)
    except TypeError:
        return False
    return True


def condition_key(condition: type[ConditionBase]) -> str:
    """Return a stable snake-case UI key without duplicating semantic aliases."""
    if condition in _KEY_OVERRIDES:
        return _KEY_OVERRIDES[condition]
    with_word_boundaries = re.sub(r"(?<!^)(?=[A-Z])", "_", condition.name)
    return with_word_boundaries.replace("-", "_").lower()


def enum_type(condition: type[ConditionBase]) -> type[IntEnum] | None:
    edit_type = condition.editing_type
    return edit_type if isinstance(edit_type, type) and issubclass(edit_type, IntEnum) else None


def condition_kind(condition: type[ConditionBase]) -> ConditionKind:
    """Translate editing_type/editing_is_mask_only into Studio's widget vocabulary."""
    if condition is C_INTCRD:
        # This mask edit also assigns a fresh coordinate group and clears C_CRD.
        return "operation"
    if condition.n_body == 2:
        return "pair"
    if condition.is_mask or condition.editing_is_mask_only:
        return "mask"
    if enum_type(condition) is not None:
        return "enum"
    edit_type = condition.editing_type
    if isinstance(edit_type, np.dtype) and edit_type.subdtype is not None:
        _, shape = edit_type.subdtype
        if shape == (3,):
            return "vector3"
    if edit_type is int:
        return "integer"
    if edit_type is float:
        return "scalar"
    raise TypeError(f"{condition.name} requires a custom Studio editor for {edit_type!r}")


def _condition_default(condition: type[ConditionBase], kind: ConditionKind) -> ConditionDefault:
    if condition in _DEFAULT_OVERRIDES:
        return _DEFAULT_OVERRIDES[condition]
    enum_cls = enum_type(condition)
    if enum_cls is not None:
        return next(iter(enum_cls)).value
    if kind == "mask":
        return False
    return None


def _count_unit(condition: type[ConditionBase]) -> CountUnit:
    if condition in _COUNT_UNIT_OVERRIDES:
        return _COUNT_UNIT_OVERRIDES[condition]
    if condition.level == Level.RESIDUE:
        return "residues"
    if condition.level == Level.CHAIN:
        return "chains"
    return "atoms"


def _enum_values(condition: type[ConditionBase]) -> tuple[ConditionValue, ...]:
    enum_cls = enum_type(condition)
    if enum_cls is None:
        return ()
    return tuple(
        ConditionValue(name=member.name, value=int(member.value)) for member in enum_cls if member.name != "NON_PROTEIN"
    )


def build_condition_definitions() -> tuple[ConditionDefinition, ...]:
    """Build Studio definitions from the live StandardAnnotation registry."""
    definitions = []
    for condition in editable_condition_classes():
        key = condition_key(condition)
        kind = condition_kind(condition)
        default = _condition_default(condition, kind)
        label = _LABEL_OVERRIDES.get(condition, key.replace("_", " ").title())
        enum_values = _enum_values(condition)
        definitions.append(
            ConditionDefinition(
                key=key,
                label=label,
                kind=kind,
                level=Level.CHAIN if condition is C_CENTROID else condition.level,
                count_unit=_count_unit(condition),
                default=default,
                invert_mask=condition is C_IDX,
                condition=condition,
                enum_values=enum_values,
            )
        )
    keys = [definition.key for definition in definitions]
    if len(keys) != len(set(keys)):
        raise ValueError(f"Condition editing contract produces duplicate Studio keys: {keys}")
    return tuple(definitions)


CONDITION_DEFINITIONS = build_condition_definitions()

_definitions_by_name: dict[str, ConditionDefinition] = {}
for definition in CONDITION_DEFINITIONS:
    names = {definition.key, definition.condition.name, definition.condition.full_name, *definition.condition.aliases}
    for name in names:
        if (existing := _definitions_by_name.get(name)) is not None and existing is not definition:
            raise ValueError(
                f"Condition editor name {name!r} resolves to both {existing.key!r} and {definition.key!r}."
            )
        _definitions_by_name[name] = definition
CONDITION_DEFINITIONS_BY_NAME = MappingProxyType(_definitions_by_name)
