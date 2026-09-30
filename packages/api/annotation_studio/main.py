"""FastAPI surface for the exact native AtomWorks editor core."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from importlib.resources import files
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from urllib.parse import urlencode

from atomworks_studio_editing.contracts import DocumentRevisionConflictError, TargetDiscardConfirmationRequiredError
from atomworks_studio_editing.loading import load_options_schema
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from .contracts import (
    NEUTRAL_CONDITION_PROVIDER,
    NEUTRAL_VALIDATION_PROVIDER,
    CapacityExceededError,
    ConditionProvider,
    IdempotencyConflictError,
    JobProvider,
    SessionNotFoundError,
    ValidationProvider,
)
from .core import BOND_TYPES, INTERACTION_TYPES, StudioDocument
from .design_jobs import RFD4_LABEL, DesignJobRequest, ProcessDesignProvider
from .identity import current_owner, reset_current_owner, set_current_owner
from .providers import ProviderSummary, load_providers
from .version import __version__

LOGGER = logging.getLogger("atomworks_studio")

MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_SESSIONS = 64
SESSION_TTL_SECONDS = 2 * 60 * 60
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
# The built-in RFD4 design jobs are registered under this provider name.
RFD4_PROVIDER = "rfd4"
# Job record fields that stay on the server.
PRIVATE_JOB_FIELDS = frozenset(
    {"idempotency_key", "metric_summary", "outputs", "owner_id", "request_fingerprint", "source_session_id"}
)
IDENTITY_HEADER = os.environ.get("ANNOTATION_STUDIO_IDENTITY_HEADER")
if IDENTITY_HEADER and os.environ.get("ANNOTATION_STUDIO_TRUST_PROXY_IDENTITY") != "1":
    raise RuntimeError(
        "ANNOTATION_STUDIO_IDENTITY_HEADER requires ANNOTATION_STUDIO_TRUST_PROXY_IDENTITY=1; "
        "the proxy must strip client-supplied identity headers"
    )


class DocumentStore:
    """Bounded in-memory documents with optional restart-safe CIF snapshots."""

    def __init__(
        self,
        session_dir: Path | None = None,
        *,
        condition_provider: ConditionProvider | None = None,
        validation_provider: ValidationProvider | None = None,
    ) -> None:
        self._documents: dict[str, tuple[float, str, StudioDocument]] = {}
        self._document_locks: dict[str, RLock] = {}
        self._lock = RLock()
        self._session_dir = session_dir
        self._condition_provider = condition_provider or NEUTRAL_CONDITION_PROVIDER
        self._validation_provider = validation_provider or NEUTRAL_VALIDATION_PROVIDER
        if self._session_dir is not None:
            self._session_dir.mkdir(parents=True, exist_ok=True)
            for stale_temporary in self._session_dir.glob(".*.tmp"):
                stale_temporary.unlink(missing_ok=True)
            probe = self._session_dir / f".write-probe-{os.getpid()}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()

    @property
    def persistent(self) -> bool:
        return self._session_dir is not None

    def _session_file(self, session_id: str) -> Path | None:
        if self._session_dir is None or SESSION_ID_PATTERN.fullmatch(session_id) is None:
            return None
        return self._session_dir / f"{session_id}.session"

    def _forget(self, session_id: str, *, delete_file: bool) -> None:
        self._documents.pop(session_id, None)
        self._document_locks.pop(session_id, None)
        if delete_file and (path := self._session_file(session_id)) is not None:
            path.unlink(missing_ok=True)

    def _prune(self, *, reserve_slot: bool = False) -> None:
        memory_cutoff = time.monotonic() - SESSION_TTL_SECONDS
        for session_id, (used, _, _) in list(self._documents.items()):
            if used < memory_cutoff:
                self._forget(session_id, delete_file=self._session_dir is None)

        if self._session_dir is not None:
            file_cutoff = time.time() - SESSION_TTL_SECONDS
            files = list(self._session_dir.glob("*.session"))
            for path in files:
                if path.stat().st_mtime < file_cutoff:
                    self._forget(path.stem, delete_file=True)
            files = sorted(self._session_dir.glob("*.session"), key=lambda path: path.stat().st_mtime)
            limit = MAX_SESSIONS - 1 if reserve_slot else MAX_SESSIONS
            while len(files) > limit:
                oldest = files.pop(0)
                self._forget(oldest.stem, delete_file=True)
            return

        limit = MAX_SESSIONS - 1 if reserve_slot else MAX_SESSIONS
        while len(self._documents) > limit:
            oldest = min(self._documents, key=lambda key: self._documents[key][0])
            self._forget(oldest, delete_file=False)

    def _persist(self, session_id: str, document: StudioDocument) -> None:
        path = self._session_file(session_id)
        if path is None:
            return
        metadata = json.dumps(
            {"name": document.name, "owner_id": current_owner()},
            separators=(",", ":"),
        )
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8") as output:
            output.write(f"{metadata}\n{document.to_cif()}")
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)

    def _load(self, session_id: str) -> tuple[str, StudioDocument] | None:
        path = self._session_file(session_id)
        if path is None or not path.is_file():
            return None
        try:
            metadata_text, cif = path.read_text(encoding="utf-8").split("\n", 1)
            metadata = json.loads(metadata_text)
            return (
                str(metadata.get("owner_id", "local")),
                StudioDocument.from_cif(
                    cif,
                    str(metadata.get("name", "structure")),
                    condition_provider=self._condition_provider,
                    validation_provider=self._validation_provider,
                ),
            )
        except (IndexError, KeyError, OSError, ValueError):
            path.unlink(missing_ok=True)
            return None

    def add(self, document: StudioDocument) -> str:
        with self._lock:
            self._prune(reserve_slot=True)
            session_id = secrets.token_urlsafe(24)
            self._documents[session_id] = (time.monotonic(), current_owner(), document)
            self._document_locks[session_id] = RLock()
            self._persist(session_id, document)
            return session_id

    def get(self, session_id: str) -> StudioDocument:
        with self._lock:
            self._prune()
            entry = self._documents.get(session_id)
            if entry is None:
                loaded = self._load(session_id)
                if loaded is None:
                    raise KeyError(session_id)
                owner_id, document = loaded
                entry = (time.monotonic(), owner_id, document)
                self._document_locks[session_id] = RLock()
            if entry[1] != current_owner():
                raise KeyError(session_id)
            self._documents[session_id] = (time.monotonic(), entry[1], entry[2])
            if (path := self._session_file(session_id)) is not None:
                path.touch()
            return entry[2]

    def call[T](
        self,
        session_id: str,
        operation: Callable[[StudioDocument], T],
        *,
        persist: bool = False,
    ) -> T:
        with self._lock:
            try:
                document = self.get(session_id)
            except KeyError:
                raise SessionNotFoundError(session_id) from None
            document_lock = self._document_locks[session_id]
        with document_lock:
            result = operation(document)
            if persist:
                self._persist(session_id, document)
            return result


session_dir_value = os.environ.get("ANNOTATION_STUDIO_SESSION_DIR")
project_root = Path(os.environ.get("ANNOTATION_STUDIO_RFD4_ROOT", Path.cwd())).resolve()
providers = load_providers(project_root)
condition_provider = providers.condition_provider
validation_provider = providers.validation_provider
condition_operations = providers.condition_operations
if os.environ.get("ANNOTATION_STUDIO_DATABASE_URL"):
    from .durable_jobs import DurableDesignProvider

    design_jobs = DurableDesignProvider.from_environment()
else:
    design_jobs = ProcessDesignProvider.from_environment(project_root)
providers.add_job_provider(RFD4_PROVIDER, design_jobs, label=RFD4_LABEL)
if os.environ.get("ANNOTATION_STUDIO_DATABASE_URL"):
    from .durable_sessions import PostgresDocumentStore

    store = PostgresDocumentStore(
        design_jobs.repository,
        condition_provider=condition_provider,
        validation_provider=validation_provider,
        ttl_seconds=int(os.environ.get("ANNOTATION_STUDIO_SESSION_TTL_SECONDS", str(SESSION_TTL_SECONDS))),
        max_sessions_per_user=int(os.environ.get("ANNOTATION_STUDIO_MAX_SESSIONS_PER_USER", str(MAX_SESSIONS))),
    )
else:
    store = DocumentStore(
        Path(session_dir_value) if session_dir_value else None,
        condition_provider=condition_provider,
        validation_provider=validation_provider,
    )
# (provider name, job id, output id or "input") -> editor session of that structure.
output_sessions: dict[tuple[str, str, str], str] = {}
output_sessions_lock = RLock()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    providers.close()


app = FastAPI(title="AtomWorks Annotation Studio", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def security_headers(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    started = time.monotonic()
    owner_id = "local"
    protected_api = request.url.path.startswith("/api/") and request.url.path not in {
        "/api/health",
        "/api/metrics",
        "/api/ready",
        "/api/v1/health",
        "/api/v1/metrics",
        "/api/v1/ready",
    }
    rejection: Response | None = None
    if IDENTITY_HEADER and protected_api:
        owner_id = request.headers.get(IDENTITY_HEADER, "").strip()
        if not owner_id:
            rejection = JSONResponse(status_code=401, content={"detail": "authenticated user identity is required"})
        elif len(owner_id) > 256 or any(ord(character) < 32 for character in owner_id):
            rejection = JSONResponse(status_code=400, content={"detail": "invalid authenticated user identity"})
    owner_token = set_current_owner(owner_id)
    request_id = request.headers.get("X-Request-ID", "")
    if re.fullmatch(r"[A-Za-z0-9._-]{8,64}", request_id) is None:
        request_id = secrets.token_hex(12)
    try:
        response = rejection or await call_next(request)
    finally:
        reset_current_owner(owner_token)
    response.headers["X-Request-ID"] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        response.headers.setdefault("Cache-Control", "no-cache")
    frame_ancestors = os.environ.get("ANNOTATION_STUDIO_FRAME_ANCESTORS", "'self'")
    response.headers["Content-Security-Policy"] = f"frame-ancestors {frame_ancestors}"
    route = request.scope.get("route")
    LOGGER.info(
        json.dumps(
            {
                "event": "http_request",
                "request_id": request_id,
                "method": request.method,
                "route": getattr(route, "path", "unmatched"),
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            },
            separators=(",", ":"),
        )
    )
    return response


def _session_response(session_id: str, structure: dict) -> dict:
    query = urlencode({"session": session_id, "embed": "1"})
    return {"session_id": session_id, "editor_path": f"/?{query}", "structure": structure}


def _public_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metrics.items()
        if (
            isinstance(value, int | float | bool)
            or key in {"dataset_name", "example_id", "task", "ckpt_name", "search.reward"}
        )
        and "path" not in key.lower()
        and not key.startswith("generation.")
    }


def _owner_id(request: Request) -> str:
    del request
    return current_owner()


def _idempotency_key(request: Request) -> str | None:
    value = request.headers.get("Idempotency-Key")
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > 128 or any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise HTTPException(status_code=400, detail="invalid Idempotency-Key")
    return value


def _job_document_session(provider_name: str, job_id: str, output_id: str, cif: str, name: str) -> dict:
    key = (provider_name, job_id, output_id)
    with output_sessions_lock:
        session_id = output_sessions.get(key)
        if session_id is not None:
            try:
                document = store.get(session_id)
                return _session_response(session_id, document.summary())
            except KeyError:
                pass
        document = StudioDocument.from_cif(
            cif,
            name,
            condition_provider=condition_provider,
            validation_provider=validation_provider,
        )
        try:
            session_id = store.add(document)
        except CapacityExceededError as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        output_sessions[key] = session_id
        return _session_response(session_id, document.summary())


def _public_output(output: dict[str, Any]) -> dict[str, Any]:
    """Return an output without server paths; artifacts are listed by name and file name."""

    public = {
        "id": output["id"],
        "label": output["label"],
        "kind": output["kind"],
        "rank": output["rank"],
        "metrics": _public_metrics(output["metrics"]),
    }
    if units := output.get("units"):
        public["units"] = units
    if artifacts := output.get("artifacts"):
        public["artifacts"] = [{"name": name, "label": Path(path).name} for name, path in artifacts.items()]
    return public


def _job_response(
    provider_name: str,
    provider: JobProvider,
    record: dict[str, Any],
    *,
    owner_id: str,
) -> dict[str, Any]:
    job_id = record["id"]
    if source_session_id := record.get("source_session_id"):
        with output_sessions_lock:
            output_sessions.setdefault((provider_name, job_id, "input"), source_session_id)
    input_session = _job_document_session(
        provider_name,
        job_id,
        "input",
        provider.input_cif(job_id, owner_id=owner_id),
        record["input_name"],
    )
    return {
        **{key: value for key, value in record.items() if key not in PRIVATE_JOB_FIELDS},
        "metric_summary": {
            key: value
            for key, value in record["metric_summary"].items()
            if "path" not in key.lower() and not key.startswith("generation.")
        },
        "input": {"id": "input", "label": record["input_name"], "kind": "input", "metrics": {}, **input_session},
        "outputs": [_public_output(output) for output in record["outputs"]],
    }


def _job_provider(name: str) -> JobProvider:
    provider = providers.job_providers.get(name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"job provider not found: {name}")
    return provider


@contextmanager
def _job_submission_errors() -> Iterator[None]:
    """Map request parsing and submission failures to HTTP errors."""

    try:
        yield
    except IdempotencyConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except CapacityExceededError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _submit_job(
    provider_name: str,
    session_id: str,
    http_request: Request,
    parse: Callable[[JobProvider], Any],
) -> dict:
    """Submit the session structure; parse runs only for an existing session and a valid Idempotency-Key."""

    provider = _job_provider(provider_name)
    owner_id = _owner_id(http_request)
    cif, name = _call(session_id, lambda document: (document.to_cif(), document.name))
    idempotency_key = _idempotency_key(http_request)
    with _job_submission_errors():
        record = provider.submit(
            cif,
            name,
            parse(provider),
            source_session_id=session_id,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
        )
    with output_sessions_lock:
        output_sessions[(provider_name, record["id"], "input")] = session_id
    return _job_response(provider_name, provider, record, owner_id=owner_id)


def _get_job(provider_name: str, job_id: str, request: Request, *, not_found: str = "job not found") -> dict:
    provider = _job_provider(provider_name)
    owner_id = _owner_id(request)
    try:
        return _job_response(provider_name, provider, provider.get(job_id, owner_id=owner_id), owner_id=owner_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=not_found) from None


def _cancel_job(provider_name: str, job_id: str, request: Request, *, not_found: str = "job not found") -> dict:
    provider = _job_provider(provider_name)
    owner_id = _owner_id(request)
    try:
        return _job_response(provider_name, provider, provider.cancel(job_id, owner_id=owner_id), owner_id=owner_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=not_found) from None


def _open_job_output(
    provider_name: str,
    job_id: str,
    output_id: str,
    request: Request,
    *,
    not_found: str = "job output not found",
) -> dict:
    provider = _job_provider(provider_name)
    owner_id = _owner_id(request)
    try:
        cif, output = provider.output_cif(job_id, output_id, owner_id=owner_id)
        session = _job_document_session(provider_name, job_id, output_id, cif, output["label"])
    except KeyError:
        raise HTTPException(status_code=404, detail=not_found) from None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {**_public_output(output), **session}


def _align_job_output(
    provider_name: str,
    job_id: str,
    output_id: str,
    request: Request,
    *,
    not_found: str = "job output not found",
) -> dict:
    provider = _job_provider(provider_name)
    owner_id = _owner_id(request)
    try:
        cif, output = provider.output_cif(job_id, output_id, owner_id=owner_id)
        session = _job_document_session(provider_name, job_id, output_id, cif, output["label"])
        reference = StudioDocument.from_cif(
            provider.input_cif(job_id, owner_id=owner_id),
            "job input",
            condition_provider=condition_provider,
            validation_provider=validation_provider,
        )
        alignment: dict[str, float | int] = {}
        response = _apply(
            session["session_id"],
            lambda document: alignment.update(document.align_to(reference)),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=not_found) from None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {**_public_output(output), "alignment": alignment, **response}


class SelectionRequest(BaseModel):
    atom_indices: list[int] = Field(min_length=1, max_length=100_000)

    @field_validator("atom_indices")
    @classmethod
    def unique_indices(cls, value: list[int]) -> list[int]:
        if any(index < 0 for index in value):
            raise ValueError("atom indices must be non-negative")
        return sorted(set(value))


class ScopedSelectionRequest(SelectionRequest):
    scope: Literal["atom", "residue", "chain"] = "atom"


class ConfirmableSelectionRequest(ScopedSelectionRequest):
    allow_target_discard: bool = False
    expected_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def confirmation_has_revision(self):
        if self.allow_target_discard and self.expected_revision is None:
            raise ValueError("Confirming target loss requires the preview's expected_revision")
        return self


class ConditionMutation(ConfirmableSelectionRequest):
    condition: str
    enabled: bool = True
    value: int | float | str | list[float] | None = None

    @field_validator("condition")
    @classmethod
    def valid_condition(cls, value: str) -> str:
        if value not in condition_provider.definitions_by_name:
            raise ValueError(f"unsupported condition: {value}")
        return value

    @field_validator("value", mode="before")
    @classmethod
    def strict_condition_value(cls, value: object, info: Any) -> object:
        definition = condition_provider.definitions_by_name.get(info.data.get("condition", ""))
        if definition is not None and definition.kind == "text" and isinstance(value, str):
            return value
        values = value if isinstance(value, list) else [value]
        if value is not None and any(isinstance(item, bool) or not isinstance(item, int | float) for item in values):
            raise ValueError("condition value must contain only JSON numbers")
        return value


class SelectionResolve(ScopedSelectionRequest):
    pass


class ConditionOperationMutation(ConfirmableSelectionRequest):
    enabled: bool = True


class GroupMutation(ConfirmableSelectionRequest):
    atom_indices: list[int] = Field(default_factory=list, max_length=1000000)
    condition: str
    action: Literal["new", "add", "remove", "delete"]
    group_id: int | None = Field(default=None, strict=True)


class CentroidMutation(BaseModel):
    condition: str
    chain: str
    value: list[float] | None = None
    expected_revision: int = Field(ge=0)


class RestraintsReplacement(BaseModel):
    pairs: list[tuple[int, int]]
    values: list[float]
    expected_revision: int = Field(ge=0)


class ExportResponse(BaseModel):
    cif: str
    name: str
    revision: int
    issues: list[dict[str, str]]


class SelectionQuery(BaseModel):
    expression: str = Field(min_length=1, max_length=4096)
    syntax: Literal["expression", "path"] = "expression"
    scope: Literal["atom", "residue", "chain"] = "atom"


class ChargeMutation(SelectionRequest):
    value: int | None = Field(default=None, ge=-10, le=10)
    delta: int | None = Field(default=None, ge=-10, le=10)


class ChainMutation(SelectionRequest):
    chain_id: str = Field(min_length=1, max_length=8)


class BondMutation(BaseModel):
    atom1: int = Field(ge=0)
    atom2: int = Field(ge=0)
    action: Literal["add", "remove"]
    bond_type: str = "SINGLE"

    @field_validator("bond_type")
    @classmethod
    def valid_bond_type(cls, value: str) -> str:
        value = value.upper()
        if value not in BOND_TYPES:
            raise ValueError(f"unsupported bond type: {value}")
        return value


class DistanceRestraintMutation(BaseModel):
    atom1: int = Field(ge=0)
    atom2: int = Field(ge=0)
    action: Literal["set", "remove"] = "set"
    distance: float | None = Field(default=None, gt=0, le=1000)

    @field_validator("distance")
    @classmethod
    def distance_required_for_set(cls, value: float | None, info: Any) -> float | None:
        if info.data.get("action", "set") == "set" and value is None:
            raise ValueError("distance is required when action is set")
        return value


class InteractionAnalysisRequest(BaseModel):
    interaction_types: list[
        Literal[
            "hbond",
            "hydrophobic",
            "pi",
            "salt",
            "halogen",
            "metal",
            "pistacking",
            "pication",
            "saltbridge",
        ]
    ] = Field(
        default_factory=lambda: ["hbond", "hydrophobic", "pi", "salt", "halogen", "metal"],
        min_length=1,
        max_length=9,
    )
    hbond_model: Literal["rosetta", "plip"] = "rosetta"
    inter_chain_only: bool = True
    source_atom_indices: list[int] = Field(default_factory=list, max_length=100_000)
    target_atom_indices: list[int] = Field(default_factory=list, max_length=100_000)

    @field_validator("interaction_types")
    @classmethod
    def unique_interaction_types(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("interaction types must be unique")
        return value

    @field_validator("source_atom_indices", "target_atom_indices")
    @classmethod
    def valid_filter_indices(cls, value: list[int]) -> list[int]:
        if any(index < 0 for index in value):
            raise ValueError("atom indices must be non-negative")
        return sorted(set(value))


class DeleteMutation(SelectionRequest):
    close_residue_gaps: bool = False


class SegmentMutation(BaseModel):
    minimum: int = Field(ge=1, le=10_000)
    maximum: int = Field(ge=1, le=10_000)
    atom_index: int | None = Field(default=None, ge=0)
    before: bool = True
    new_chain: bool = False
    chain_id: str | None = Field(default=None, max_length=8)
    chain_type: str | None = None


class RealizeMutation(BaseModel):
    seed: int | None = None


class CombineMutation(BaseModel):
    session_ids: list[str] = Field(min_length=1, max_length=16)


def _call[T](
    session_id: str,
    operation: Callable[[StudioDocument], T],
    *,
    persist: bool = False,
) -> T:
    try:
        return store.call(session_id, operation, persist=persist)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="session not found or expired") from None


def _apply(session_id: str, operation: Callable[[StudioDocument], Any]) -> dict:
    try:
        previous_revision, result, structure = _call(
            session_id,
            lambda document: (document.revision, operation(document), document.summary()),
            persist=True,
        )
    except TargetDiscardConfirmationRequiredError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "target_discard_confirmation_required",
                "lost_target_count": error.lost_target_count,
                "revision": error.revision,
                "message": str(error),
            },
        ) from error
    except DocumentRevisionConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "revision_conflict", "revision": error.revision, "message": str(error)},
        ) from error
    except (ValueError, IndexError, KeyError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        **_session_response(session_id, structure),
        "edit_result": {
            **(result if isinstance(result, dict) else {}),
            "changed": structure["revision"] != previous_revision,
        },
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": os.environ.get("ANNOTATION_STUDIO_VERSION", __version__),
        "revision": os.environ.get("ANNOTATION_STUDIO_REVISION", "unknown"),
        "session_store": "persistent" if store.persistent else "memory",
        "design_jobs": "enabled" if design_jobs.enabled else "disabled",
    }


@app.get("/api/ready")
def ready() -> dict[str, str]:
    healthcheck = getattr(design_jobs, "healthcheck", None)
    if healthcheck is not None:
        try:
            healthcheck()
        except Exception as error:
            LOGGER.warning("durable backend is not ready: %s", error)
            raise HTTPException(status_code=503, detail="durable backend is not ready") from error
    return {"status": "ready"}


@app.get("/api/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics(request: Request) -> PlainTextResponse:
    token = os.environ.get("ANNOTATION_STUDIO_METRICS_TOKEN")
    operational_metrics = getattr(design_jobs, "operational_metrics", None)
    if not token or operational_metrics is None:
        raise HTTPException(status_code=404, detail="metrics are not configured")
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer ") or not secrets.compare_digest(authorization[7:], token):
        raise HTTPException(status_code=401, detail="invalid metrics credentials")
    values = operational_metrics()
    lines = []
    for name, value in sorted(values.items()):
        metric_name = f"annotation_studio_{name}"
        lines.extend((f"# TYPE {metric_name} gauge", f"{metric_name} {value}"))
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.get("/api/schema")
def schema() -> dict:
    return {
        "conditions": {definition.key: definition.schema for definition in condition_provider.definitions},
        "bond_types": sorted(BOND_TYPES),
        "loading": load_options_schema(),
    }


@app.get("/api/design/schema")
def design_schema() -> dict:
    return _job_provider(RFD4_PROVIDER).schema()


@app.post("/api/sessions/{session_id}/design-jobs")
def submit_design_job(session_id: str, job_request: DesignJobRequest, http_request: Request) -> dict:
    return _submit_job(RFD4_PROVIDER, session_id, http_request, lambda _provider: job_request)


@app.get("/api/design-jobs/{job_id}")
def get_design_job(job_id: str, request: Request) -> dict:
    return _get_job(RFD4_PROVIDER, job_id, request, not_found="design job not found")


@app.post("/api/design-jobs/{job_id}/cancel")
def cancel_design_job(job_id: str, request: Request) -> dict:
    return _cancel_job(RFD4_PROVIDER, job_id, request, not_found="design job not found")


@app.post("/api/design-jobs/{job_id}/outputs/{output_id}/open")
def open_design_output(job_id: str, output_id: str, request: Request) -> dict:
    return _open_job_output(RFD4_PROVIDER, job_id, output_id, request, not_found="design output not found")


@app.post("/api/design-jobs/{job_id}/outputs/{output_id}/align-to-input")
def align_design_output(job_id: str, output_id: str, request: Request) -> dict:
    return _align_job_output(RFD4_PROVIDER, job_id, output_id, request, not_found="design output not found")


@app.get("/api/providers")
def list_providers() -> list[ProviderSummary]:
    return providers.summaries()


@app.get("/api/providers/{name}/jobs/schema")
def provider_job_schema(name: str) -> dict:
    return _job_provider(name).schema()


@app.post("/api/sessions/{session_id}/providers/{name}/jobs")
def submit_provider_job(
    session_id: str,
    name: str,
    http_request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict:
    return _submit_job(name, session_id, http_request, lambda provider: provider.parse_request(payload))


@app.get("/api/providers/{name}/jobs/{job_id}")
def get_provider_job(name: str, job_id: str, request: Request) -> dict:
    return _get_job(name, job_id, request)


@app.post("/api/providers/{name}/jobs/{job_id}/cancel")
def cancel_provider_job(name: str, job_id: str, request: Request) -> dict:
    return _cancel_job(name, job_id, request)


@app.post("/api/providers/{name}/jobs/{job_id}/outputs/{output_id}/open")
def open_provider_job_output(name: str, job_id: str, output_id: str, request: Request) -> dict:
    return _open_job_output(name, job_id, output_id, request)


@app.post("/api/providers/{name}/jobs/{job_id}/outputs/{output_id}/align-to-input")
def align_provider_job_output(name: str, job_id: str, output_id: str, request: Request) -> dict:
    return _align_job_output(name, job_id, output_id, request)


@app.get(
    "/api/providers/{name}/jobs/{job_id}/outputs/{output_id}/artifacts/{artifact}",
    response_class=FileResponse,
)
def download_provider_job_artifact(
    name: str,
    job_id: str,
    output_id: str,
    artifact: str,
    request: Request,
) -> FileResponse:
    provider = _job_provider(name)
    try:
        path, media_type = provider.artifact(job_id, output_id, artifact, owner_id=_owner_id(request))
    except KeyError:
        raise HTTPException(status_code=404, detail="job artifact not found") from None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.post("/api/sessions")
async def create_session(
    file: UploadFile = File(...),
    parse_preset: Literal["minimal", "rcsb"] = Form("minimal"),
    assembly: str = Form("1"),
    model: int | None = Form(None),
    remove_waters: bool | None = Form(None),
    hydrogen_policy: Literal["keep", "remove"] | None = Form(None),
    advanced_options: str = Form("{}"),
) -> dict:
    filename = Path(file.filename or "structure.cif").name
    if not filename.lower().endswith((".cif", ".mmcif")):
        raise HTTPException(status_code=415, detail="only CIF/mmCIF uploads are accepted")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"upload exceeds {MAX_UPLOAD_BYTES // 1024 // 1024} MiB")
    try:
        text = content.decode("utf-8")
        document = StudioDocument.from_cif(
            text,
            Path(filename).stem,
            parse_options={
                "preset": parse_preset,
                "assembly": assembly,
                "model": model,
                "remove_waters": remove_waters,
                "hydrogen_policy": hydrogen_policy,
                "advanced_options": json.loads(advanced_options),
            },
            condition_provider=condition_provider,
            validation_provider=validation_provider,
        )
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"invalid annotated CIF: {error}") from error
    try:
        session_id = store.add(document)
    except CapacityExceededError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    return _session_response(session_id, document.summary())


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    return _session_response(session_id, _call(session_id, lambda document: document.summary()))


@app.get("/api/sessions/{session_id}/annotations/{condition}")
def get_annotation(session_id: str, condition: str) -> dict:
    try:
        details = _call(session_id, lambda document: document.annotation_details(condition))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"annotation": details[0] if details else None}


@app.get("/api/sessions/{session_id}/annotation-fields/{field_name}")
def get_annotation_field(session_id: str, field_name: str) -> dict:
    details = _call(session_id, lambda document: document.stored_annotation_fields(field_name))
    if not details:
        raise HTTPException(status_code=404, detail=f"annotation field not found: {field_name}")
    return {"annotation": details[0]}


@app.get("/api/sessions/{session_id}/scalar-fields/{field_name}")
def get_scalar_field(session_id: str, field_name: str) -> dict:
    details = _call(session_id, lambda document: document.scalar_annotation_fields(field_name))
    if not details:
        raise HTTPException(status_code=404, detail=f"scalar field not found: {field_name}")
    return {"annotation": details[0]}


@app.get("/api/sessions/{session_id}/atoms/{atom_index}/annotations")
def get_atom_annotations(session_id: str, atom_index: int) -> dict:
    try:
        return _call(session_id, lambda document: document.atom_annotation_details(atom_index))
    except IndexError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/api/sessions/{session_id}/interactions/analyze")
def analyze_interactions(session_id: str, request: InteractionAnalysisRequest) -> dict:
    expanded_types: list[str] = []
    for interaction_type in request.interaction_types:
        expanded_types.extend(
            {
                "pi": ["pistacking", "pication"],
                "salt": ["saltbridge"],
            }.get(interaction_type, [interaction_type])
        )
    interaction_types = tuple(dict.fromkeys(expanded_types))
    if not set(interaction_types) <= set(INTERACTION_TYPES):
        raise HTTPException(status_code=422, detail="unsupported interaction type")
    try:
        return _call(
            session_id,
            lambda document: document.analyze_interactions(
                interaction_types=interaction_types,
                hbond_model=request.hbond_model,
                inter_chain_only=request.inter_chain_only,
                source_atom_indices=tuple(request.source_atom_indices),
                target_atom_indices=tuple(request.target_atom_indices),
            ),
        )
    except (IndexError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/sessions/{session_id}/selections/resolve")
def resolve_selection(session_id: str, selection: SelectionResolve) -> dict:
    try:
        indices = _call(
            session_id,
            lambda document: document.resolve_indices(selection.atom_indices, selection.scope),
        )
    except (ValueError, IndexError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"atom_indices": indices.tolist(), "count": len(indices)}


@app.post("/api/sessions/{session_id}/selections/query")
def query_selection(session_id: str, selection: SelectionQuery) -> dict:
    try:
        indices = _call(
            session_id,
            lambda document: document.query_indices(
                selection.expression,
                syntax=selection.syntax,
                scope=selection.scope,
            ),
        )
    except (IndexError, NameError, SyntaxError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"atom_indices": indices.tolist(), "count": len(indices)}


@app.get("/api/sessions/{session_id}/cif", response_class=PlainTextResponse)
def get_cif(session_id: str) -> PlainTextResponse:
    cif, name = _call(session_id, lambda document: (document.to_cif(), document.name))
    return PlainTextResponse(
        cif,
        media_type="chemical/x-mmcif",
        headers={"Content-Disposition": f'attachment; filename="{name}_annotated.cif"'},
    )


@app.post("/api/sessions/{session_id}/export")
def export_cif(session_id: str) -> ExportResponse:
    # Validate and export one snapshot. Later edits cannot change the approved file.
    document = _call(session_id, lambda current: current.clone())
    issues = document.validate(full_pipeline=True)
    return ExportResponse(cif=document.to_cif(), name=document.name, revision=document.revision, issues=issues)


@app.post("/api/sessions/{session_id}/conditions")
def set_condition(session_id: str, mutation: ConditionMutation) -> dict:
    return _apply(
        session_id,
        lambda document: document.set_condition(
            mutation.condition,
            mutation.atom_indices,
            enabled=mutation.enabled,
            value=mutation.value,
            scope=mutation.scope,
            allow_target_discard=mutation.allow_target_discard,
            expected_revision=mutation.expected_revision,
        ),
    )


@app.post("/api/sessions/{session_id}/groups")
def edit_group(session_id: str, mutation: GroupMutation) -> dict:
    return _apply(
        session_id,
        lambda document: document.edit_condition_group(
            mutation.condition,
            mutation.action,
            mutation.atom_indices,
            group_id=mutation.group_id,
            scope=mutation.scope,
            allow_target_discard=mutation.allow_target_discard,
            expected_revision=mutation.expected_revision,
        ),
    )


@app.post("/api/sessions/{session_id}/centroids")
def edit_centroid(session_id: str, mutation: CentroidMutation) -> dict:
    return _apply(
        session_id,
        lambda document: document.edit_chain_centroid(
            mutation.condition,
            mutation.chain,
            mutation.value,
            expected_revision=mutation.expected_revision,
        ),
    )


@app.post("/api/sessions/{session_id}/distance-restraints/replace")
def replace_restraints(session_id: str, mutation: RestraintsReplacement) -> dict:
    return _apply(
        session_id,
        lambda document: document.replace_distance_restraints(
            mutation.pairs,
            mutation.values,
            expected_revision=mutation.expected_revision,
        ),
    )


@app.get("/api/sessions/{session_id}/atom-distance")
def atom_distance(session_id: str, atom1: int, atom2: int) -> dict:
    def inspect(document):
        return {
            "revision": document.revision,
            "distance": document.atom_distance(atom1, atom2),
            "atom1_label": document.atom_label(atom1),
            "atom2_label": document.atom_label(atom2),
        }

    try:
        return _call(session_id, inspect)
    except (IndexError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/sessions/{session_id}/charges")
def set_charge(session_id: str, mutation: ChargeMutation) -> dict:
    return _apply(
        session_id,
        lambda document: document.set_charge(mutation.atom_indices, value=mutation.value, delta=mutation.delta),
    )


@app.post("/api/sessions/{session_id}/chains")
def assign_chain(session_id: str, mutation: ChainMutation) -> dict:
    return _apply(
        session_id,
        lambda document: document.assign_chain(mutation.atom_indices, mutation.chain_id),
    )


@app.post("/api/sessions/{session_id}/bonds")
def edit_bond(session_id: str, mutation: BondMutation) -> dict:
    return _apply(
        session_id,
        lambda document: document.edit_bond(
            mutation.atom1,
            mutation.atom2,
            mutation.action,
            mutation.bond_type,
        ),
    )


@app.get("/api/sessions/{session_id}/bonds")
def inspect_bonds(session_id: str, atom_index: int | None = None) -> dict:
    try:
        bonds = _call(session_id, lambda document: document.inspect_bonds(atom_index))
    except IndexError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"bonds": bonds, "count": len(bonds)}


@app.post("/api/sessions/{session_id}/bonds/infer-ccd")
def infer_bonds_from_ccd(session_id: str) -> dict:
    return _apply(session_id, lambda document: document.infer_bonds_from_ccd())


@app.post("/api/sessions/{session_id}/internal-coords")
def set_internal_coordinates(session_id: str, mutation: ConditionOperationMutation) -> dict:
    transform = condition_operations.get("internal_coordinate")
    if transform is None:
        raise HTTPException(status_code=422, detail="internal-coordinate conditioning is not available")
    return _apply(
        session_id,
        lambda document: document.apply_selection_transform(
            mutation.atom_indices,
            mutation.scope,
            transform,
            mutation.enabled,
            allow_target_discard=mutation.allow_target_discard,
            expected_revision=mutation.expected_revision,
        ),
    )


@app.post("/api/sessions/{session_id}/internal-coords/clear-targets")
def clear_internal_targets(session_id: str, mutation: ConditionOperationMutation) -> dict:
    transform = condition_operations.get("clear_internal_targets")
    if transform is None:
        raise HTTPException(status_code=422, detail="internal-coordinate target editing is not available")
    return _apply(
        session_id,
        lambda document: document.apply_selection_transform(
            mutation.atom_indices,
            mutation.scope,
            transform,
            True,
            allow_target_discard=True,
            expected_revision=mutation.expected_revision,
        ),
    )


@app.get("/api/sessions/{session_id}/distance-restraints")
def get_distance_restraints(session_id: str) -> dict:
    restraints = _call(session_id, lambda document: document.distance_restraints())
    return {"restraints": restraints, "count": len(restraints)}


@app.post("/api/sessions/{session_id}/distance-restraints")
def edit_distance_restraint(session_id: str, mutation: DistanceRestraintMutation) -> dict:
    return _apply(
        session_id,
        lambda document: document.edit_distance_restraint(
            mutation.atom1,
            mutation.atom2,
            mutation.action,
            mutation.distance,
        ),
    )


@app.post("/api/sessions/{session_id}/delete")
def delete_atoms(session_id: str, mutation: DeleteMutation) -> dict:
    return _apply(
        session_id,
        lambda document: document.delete_atoms(mutation.atom_indices, mutation.close_residue_gaps),
    )


@app.post("/api/sessions/{session_id}/segments")
def insert_segment(session_id: str, mutation: SegmentMutation) -> dict:
    if mutation.maximum < mutation.minimum:
        raise HTTPException(status_code=422, detail="maximum must be at least minimum")
    return _apply(
        session_id,
        lambda document: document.insert_segment(
            mutation.minimum,
            mutation.maximum,
            mutation.atom_index,
            before=mutation.before,
            new_chain=mutation.new_chain,
            chain_id=mutation.chain_id,
            chain_type=mutation.chain_type,
        ),
    )


@app.post("/api/sessions/{session_id}/segments/realize")
def realize_segments(session_id: str, mutation: RealizeMutation) -> dict:
    return _apply(session_id, lambda document: document.realize_segments(mutation.seed))


@app.post("/api/sessions/{session_id}/segments/collapse")
def collapse_segments(session_id: str) -> dict:
    return _apply(session_id, lambda document: document.collapse_segments())


@app.post("/api/sessions/{session_id}/reset-residue-ids")
def reset_residue_ids(session_id: str) -> dict:
    return _apply(session_id, lambda document: document.reset_residue_ids())


@app.post("/api/sessions/{session_id}/undo")
def undo(session_id: str) -> dict:
    structure = _call(
        session_id,
        lambda document: document.summary() if document.undo() else None,
        persist=True,
    )
    if structure is None:
        raise HTTPException(status_code=409, detail="undo history is empty")
    return {"session_id": session_id, "structure": structure}


@app.post("/api/sessions/{session_id}/combine")
def combine(session_id: str, mutation: CombineMutation) -> dict:
    if session_id in mutation.session_ids:
        raise HTTPException(status_code=422, detail="cannot combine a session with itself")
    others = [_call(key, lambda document: document.clone()) for key in mutation.session_ids]
    return _apply(session_id, lambda document: document.combine(others))


@app.post("/api/sessions/{session_id}/validate")
def validate(session_id: str, full: bool = False) -> dict:
    return {"issues": _call(session_id, lambda document: document.validate(full_pipeline=full))}


for legacy_route in list(app.routes):
    if not isinstance(legacy_route, APIRoute) or not legacy_route.path.startswith("/api/"):
        continue
    versioned_path = "/api/v1" + legacy_route.path.removeprefix("/api")
    include_versioned_in_schema = legacy_route.include_in_schema
    legacy_route.include_in_schema = False
    app.add_api_route(
        versioned_path,
        legacy_route.endpoint,
        methods=list(legacy_route.methods or ()),
        response_model=legacy_route.response_model,
        status_code=legacy_route.status_code,
        response_class=legacy_route.response_class,
        tags=legacy_route.tags,
        name=f"v1_{legacy_route.name}",
        include_in_schema=include_versioned_in_schema,
    )


packaged_web = files("annotation_studio").joinpath("web")
source_web = Path(__file__).resolve().parents[2] / "web" / "dist"
web_dist = source_web if source_web.is_dir() else Path(str(packaged_web))
if web_dist.is_dir():
    app.mount("/", StaticFiles(directory=web_dist, html=True), name="studio")
