"""Stable provider contracts for model-specific Annotation Studio integrations."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Protocol

from atomworks.io.utils.atom_array_plus import AtomArrayPlus
from atomworks.io.utils.standard_annotations.base import Level
from atomworks.ml.conditions.base import ConditionBase

ConditionKind = Literal["mask", "enum", "integer", "scalar", "vector3", "pair", "operation"]
CountUnit = Literal["atoms", "residues", "chains", "groups", "pairs"]
ConditionDefault = bool | int | float | None
ConditionOperation = Callable[[AtomArrayPlus, Iterable[int], bool], None]


class SessionNotFoundError(KeyError):
    """A session store could not resolve a session for the current owner."""


class CapacityExceededError(RuntimeError):
    """A bounded local or shared resource has reached its configured limit."""


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
    kind: ConditionKind
    level: Level
    count_unit: CountUnit
    default: ConditionDefault
    invert_mask: bool
    condition: type[ConditionBase]
    enum_values: tuple[ConditionValue, ...] = ()

    @property
    def schema(self) -> dict[str, object]:
        """Return the JSON-facing editor schema without duplicated stored state."""
        schema: dict[str, object] = {
            "kind": self.kind,
            "level": self.level.value,
            "default": self.default,
            "label": self.label,
        }
        if self.enum_values:
            schema["values"] = {member.name: member.value for member in self.enum_values}
        return schema


class ConditionProvider(Protocol):
    """Supplies model-specific conditions without coupling the editor core."""

    @property
    def definitions(self) -> tuple[ConditionDefinition, ...]: ...

    @property
    def definitions_by_name(self) -> Mapping[str, ConditionDefinition]: ...

    def after_condition_update(self, name: str, array: AtomArrayPlus) -> None: ...


class ValidationProvider(Protocol):
    """Runs model-specific checks after the editor's generic validation."""

    def validate(self, array: AtomArrayPlus, *, full_pipeline: bool = False) -> list[dict[str, str]]: ...


class NeutralConditionProvider:
    """Condition provider for model-neutral document operations."""

    definitions: tuple[ConditionDefinition, ...] = ()
    definitions_by_name: Mapping[str, ConditionDefinition] = MappingProxyType({})

    def after_condition_update(self, name: str, array: AtomArrayPlus) -> None:
        del name, array


class NeutralValidationProvider:
    """Validation provider that adds no model-specific checks."""

    def validate(self, array: AtomArrayPlus, *, full_pipeline: bool = False) -> list[dict[str, str]]:
        del array, full_pipeline
        return []


NEUTRAL_CONDITION_PROVIDER = NeutralConditionProvider()
NEUTRAL_VALIDATION_PROVIDER = NeutralValidationProvider()


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
        owner_id: str = "local",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...

    def get(self, job_id: str, *, owner_id: str = "local") -> dict[str, Any]: ...

    def cancel(self, job_id: str, *, owner_id: str = "local") -> dict[str, Any]: ...

    def output_cif(
        self,
        job_id: str,
        output_id: str,
        *,
        owner_id: str = "local",
    ) -> tuple[str, dict[str, Any]]: ...

    def input_cif(self, job_id: str, *, owner_id: str = "local") -> str: ...

    def close(self) -> None: ...
