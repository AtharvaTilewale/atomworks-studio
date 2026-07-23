"""Stable provider contracts for model-specific Annotation Studio integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from atomworks.io.utils.atom_array_plus import AtomArrayPlus


@dataclass(frozen=True, slots=True)
class ConditionValue:
    """One named value accepted by an enum-style condition."""

    name: str
    value: int


@dataclass(frozen=True, slots=True)
class ConditionDefinition:
    """UI-neutral condition metadata and its AtomWorks condition object."""

    key: str
    label: str
    kind: str
    count_unit: str
    default: Any
    invert_mask: bool
    condition: Any
    schema: dict[str, Any]
    enum_values: tuple[ConditionValue, ...] = ()


class ConditionProvider(Protocol):
    """Supplies model-specific conditions without coupling the editor core."""

    @property
    def definitions(self) -> tuple[ConditionDefinition, ...]: ...

    @property
    def definitions_by_key(self) -> dict[str, ConditionDefinition]: ...

    def after_condition_update(self, name: str, array: AtomArrayPlus) -> None: ...


class ValidationProvider(Protocol):
    """Runs model-specific checks after the editor's generic validation."""

    def validate(self, array: AtomArrayPlus, *, full_pipeline: bool = False) -> list[dict[str, str]]: ...


class DesignProvider(Protocol):
    """Submits designs through a process or remote service boundary."""

    @property
    def enabled(self) -> bool: ...

    def schema(self) -> dict[str, Any]: ...

    def submit(
        self,
        cif: str,
        name: str,
        request: Any,
        *,
        source_session_id: str | None = None,
    ) -> dict[str, Any]: ...

    def get(self, job_id: str) -> dict[str, Any]: ...

    def output_cif(self, job_id: str, output_id: str) -> tuple[str, dict[str, Any]]: ...

    def input_cif(self, job_id: str) -> str: ...

    def close(self) -> None: ...
