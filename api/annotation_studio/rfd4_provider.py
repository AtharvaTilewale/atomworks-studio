"""RFD4-specific providers for the otherwise model-neutral Studio API."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from atomworks.io.utils.atom_array_plus import AtomArrayPlus

# AtomWorks must initialize before RFD4 reads site-specific environment files.
os.environ.setdefault("RFPROTEINA_LIGHTWEIGHT_IMPORT", "1")
from rfproteina.conditions import C_IDXGRP
from rfproteina.condition_specs import INTERACTIVE_CONDITION_SPECS
from rfproteina.interactive_validation import validate_rfd4

from .contracts import (
    ConditionDefinition,
    ConditionProvider,
    ConditionValue,
    ValidationProvider,
)
from .design_jobs import ProcessDesignProvider


class RFD4ConditionProvider(ConditionProvider):
    """Expose the canonical RFD4 condition registry through Studio contracts."""

    def __init__(self) -> None:
        self._definitions = tuple(
            ConditionDefinition(
                key=spec.key,
                label=spec.label,
                kind=spec.kind,
                count_unit=spec.count_unit,
                default=spec.default,
                invert_mask=spec.invert_mask,
                condition=spec.condition,
                schema=spec.schema(),
                enum_values=tuple(
                    ConditionValue(name=member.name, value=int(member.value)) for member in (spec.enum_cls or ())
                ),
            )
            for spec in INTERACTIVE_CONDITION_SPECS
        )
        self._definitions_by_key = {definition.key: definition for definition in self._definitions}

    @property
    def definitions(self) -> tuple[ConditionDefinition, ...]:
        return self._definitions

    @property
    def definitions_by_key(self) -> dict[str, ConditionDefinition]:
        return self._definitions_by_key

    def after_condition_update(self, name: str, array: AtomArrayPlus) -> None:
        if name != "index":
            return
        default_groups = C_IDXGRP.default_annotation(array)
        current_groups = C_IDXGRP.annotation(array, default="generate")
        combined = np.stack([default_groups, current_groups], axis=1)
        _, new_groups = np.unique(combined, axis=0, return_inverse=True)
        C_IDXGRP.set_annotation(array, new_groups)


class RFD4ValidationProvider(ValidationProvider):
    """RFD4 semantic and optional full-pipeline validation."""

    def validate(self, array: AtomArrayPlus, *, full_pipeline: bool = False) -> list[dict[str, str]]:
        return validate_rfd4(array, full_pipeline=full_pipeline)


CONDITION_PROVIDER = RFD4ConditionProvider()
VALIDATION_PROVIDER = RFD4ValidationProvider()


def create_design_provider(project_root: Path) -> ProcessDesignProvider:
    """Create the process-isolated RFD4 design provider."""

    return ProcessDesignProvider.from_environment(project_root)
