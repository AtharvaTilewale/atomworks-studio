"""Service and job contracts, plus compatibility exports for document providers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from atomworks_studio_editing.contracts import (
    NEUTRAL_CONDITION_PROVIDER as NEUTRAL_CONDITION_PROVIDER,
)
from atomworks_studio_editing.contracts import (
    NEUTRAL_VALIDATION_PROVIDER as NEUTRAL_VALIDATION_PROVIDER,
)
from atomworks_studio_editing.contracts import (
    ConditionDefault as ConditionDefault,
)
from atomworks_studio_editing.contracts import (
    ConditionDefinition as ConditionDefinition,
)
from atomworks_studio_editing.contracts import (
    ConditionKind as ConditionKind,
)
from atomworks_studio_editing.contracts import (
    ConditionOperation as ConditionOperation,
)
from atomworks_studio_editing.contracts import (
    ConditionProvider as ConditionProvider,
)
from atomworks_studio_editing.contracts import (
    ConditionValue as ConditionValue,
)
from atomworks_studio_editing.contracts import (
    CountUnit as CountUnit,
)
from atomworks_studio_editing.contracts import (
    NeutralConditionProvider as NeutralConditionProvider,
)
from atomworks_studio_editing.contracts import (
    NeutralValidationProvider as NeutralValidationProvider,
)
from atomworks_studio_editing.contracts import (
    ValidationProvider as ValidationProvider,
)


class SessionNotFoundError(KeyError):
    """A session store could not resolve a session for the current owner."""


class CapacityExceededError(RuntimeError):
    """A bounded local or shared resource has reached its configured limit."""


class IdempotencyConflictError(ValueError):
    """The same owner reused an idempotency key for a different submission."""


class JobProvider(Protocol):
    """Runs one provider's jobs through a process or remote service boundary.

    The schema includes at least "enabled", "provider", and "label". parse_request raises
    ValueError for an invalid request. artifact returns an output file and its media type.
    Lookups raise KeyError for jobs, outputs, and artifacts that do not exist or belong to
    another owner, and ValueError for output files outside the job's workspace.
    """

    @property
    def enabled(self) -> bool: ...

    def schema(self) -> dict[str, Any]: ...

    def parse_request(self, payload: dict[str, Any]) -> Any: ...

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

    def artifact(self, job_id: str, output_id: str, name: str, *, owner_id: str = "local") -> tuple[Path, str]: ...

    def close(self) -> None: ...


# The original name of the job contract, from when RFD4 was the only job provider.
DesignProvider = JobProvider
