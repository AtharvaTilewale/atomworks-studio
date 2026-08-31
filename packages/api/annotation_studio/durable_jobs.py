"""PostgreSQL queue, object storage, and leased workers for shared GPU jobs."""

from __future__ import annotations

import copy
import math
import os
import shutil
import signal
import subprocess
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

import yaml

from .contracts import CapacityExceededError
from .design_jobs import (
    AF3_ENVIRONMENT_KEYS,
    ANSI_ESCAPE,
    REFOLDING_METRIC_TAGS,
    DesignJobRequest,
    IdempotencyConflictError,
    SearchOptions,
    build_rfd4_config,
    collect_outputs,
    design_request_fingerprint,
    request_uses_refolding,
    summarize_metrics,
)

WORKER_CHILD_ENV_KEYS = {
    "CONDA_PREFIX",
    "CUDA_VISIBLE_DEVICES",
    "HOME",
    "LANG",
    "LC_ALL",
    "LD_LIBRARY_PATH",
    "NVIDIA_DRIVER_CAPABILITIES",
    "NVIDIA_VISIBLE_DEVICES",
    "PATH",
    "PYTHONPATH",
    "TMPDIR",
    "VIRTUAL_ENV",
    "XDG_CACHE_HOME",
    "AF3_LAUNCHER_SOCKET_TEMPLATE",
    *AF3_ENVIRONMENT_KEYS,
}


def _require_psycopg() -> tuple[Any, Any, Any]:
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
    except ImportError as error:  # pragma: no cover - exercised by deployment packaging
        raise RuntimeError("durable jobs require the 'durable' API extra (psycopg and psycopg-pool)") from error
    return psycopg, dict_row, Jsonb


class ObjectStore(Protocol):
    """Small capability-scoped blob-store interface used by API and workers."""

    def put(self, key: str, content: bytes, *, content_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class LocalObjectStore:
    """Filesystem object store for integration tests and one-host deployments."""

    root: Path

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError("object key escaped the configured root")
        return path

    def put(self, key: str, content: bytes, *, content_type: str) -> None:
        del content_type
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(content)
        temporary.chmod(0o600)
        temporary.replace(path)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3ObjectStore:
    """S3-compatible object store with an optional endpoint for MinIO-style services."""

    def __init__(self, bucket: str, prefix: str = "", *, endpoint_url: str | None = None) -> None:
        try:
            import boto3
        except ImportError as error:  # pragma: no cover - exercised by deployment packaging
            raise RuntimeError("S3 object storage requires the 'durable' API extra (boto3)") from error
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = boto3.client("s3", endpoint_url=endpoint_url)

    def _key(self, key: str) -> str:
        return "/".join(part for part in (self.prefix, key.strip("/")) if part)

    def put(self, key: str, content: bytes, *, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=content, ContentType=content_type)

    def get(self, key: str) -> bytes:
        body = self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))


def object_store_from_url(url: str) -> ObjectStore:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return LocalObjectStore(Path(unquote(parsed.path)))
    if parsed.scheme == "s3" and parsed.netloc:
        return S3ObjectStore(
            parsed.netloc,
            parsed.path,
            endpoint_url=os.environ.get("ANNOTATION_STUDIO_S3_ENDPOINT_URL"),
        )
    raise ValueError("object store URL must be file:///absolute/path or s3://bucket/prefix")


def worker_child_environment(project_root: Path) -> dict[str, str]:
    """Build a model environment without API, database, object-store, or cloud credentials."""

    allowed = set(WORKER_CHILD_ENV_KEYS)
    configured = os.environ.get("ANNOTATION_STUDIO_WORKER_CHILD_ENV_ALLOWLIST", "")
    for name in configured.split(","):
        name = name.strip()
        if not name:
            continue
        normalized_name = name.upper()
        if not name.replace("_", "").isalnum() or normalized_name.startswith(
            ("ANNOTATION_STUDIO_", "AWS_", "AZURE_", "GOOGLE_")
        ):
            raise ValueError(f"unsafe worker child environment name: {name}")
        allowed.add(name)
    return {
        **{name: os.environ[name] for name in allowed if name in os.environ},
        "PROJECT_ROOT": str(project_root),
    }


def _signal_process(process: subprocess.Popen[str], signal_number: signal.Signals) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal_number)
        except ProcessLookupError:
            pass
    elif signal_number == signal.SIGTERM:  # pragma: no cover - GPU workers are Linux
        process.terminate()
    else:  # pragma: no cover - GPU workers are Linux
        process.kill()


def _stage_object_deletions(connection: Any, keys: Sequence[str]) -> None:
    unique_keys = sorted({key for key in keys if key})
    if unique_keys:
        connection.execute(
            """
            INSERT INTO studio_pending_object_deletions (object_key)
            SELECT unnest(%s::text[])
            ON CONFLICT (object_key) DO NOTHING
            """,
            (unique_keys,),
        )


def _record(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    if isinstance(result.get("request"), dict):
        result["request"] = {key: value for key, value in result["request"].items() if key != "config_yaml"}
    for key in ("created_at", "updated_at", "started_at", "completed_at", "heartbeat_at"):
        value = result.get(key)
        result[key] = value.timestamp() if value is not None else None
    result["id"] = str(result["id"])
    result.pop("input_object_key", None)
    result.pop("log_object_key", None)
    result.pop("lease_owner", None)
    result.pop("lease_token", None)
    result.pop("lease_expires_at", None)
    result.pop("cancel_requested_at", None)
    result.pop("required_capabilities", None)
    result.pop("required_revision", None)
    result.pop("request_fingerprint", None)
    result.pop("attempt_started_at", None)
    result.pop("priority", None)
    result.pop("max_attempts", None)
    return result


class PostgresJobRepository:
    """Transactional job state with fair claims and expiring worker leases."""

    def __init__(self, dsn: str, *, min_pool_size: int = 1, max_pool_size: int = 10) -> None:
        _, dict_row, _ = _require_psycopg()
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as error:  # pragma: no cover
            raise RuntimeError("durable jobs require psycopg-pool") from error
        self.pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_pool_size,
            max_size=max_pool_size,
            kwargs={"row_factory": dict_row},
            open=True,
        )

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self.pool.connection() as connection:
            yield connection

    def migrate(self) -> list[str]:
        applied: list[str] = []
        migration_root = files("annotation_studio.migrations")
        with self.connection() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtext('atomworks-studio-migrations'))")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS studio_schema_migrations "
                "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            for resource in sorted(migration_root.iterdir(), key=lambda item: item.name):
                if not resource.name.endswith(".sql"):
                    continue
                exists = connection.execute(
                    "SELECT 1 FROM studio_schema_migrations WHERE version = %s",
                    (resource.name,),
                ).fetchone()
                if exists:
                    continue
                connection.execute(resource.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO studio_schema_migrations (version) VALUES (%s)",
                    (resource.name,),
                )
                applied.append(resource.name)
        return applied

    def create(
        self,
        *,
        job_id: uuid.UUID,
        owner_id: str,
        idempotency_key: str | None,
        request_fingerprint: str,
        input_name: str,
        source_session_id: str | None,
        request: dict[str, Any],
        input_object_key: str,
        required_capabilities: Sequence[str],
        required_revision: str | None,
        default_max_queued: int,
        default_max_running: int,
        max_attempts: int,
    ) -> tuple[dict[str, Any], bool]:
        _, _, jsonb = _require_psycopg()
        with self.connection() as connection:
            with connection.transaction():
                if idempotency_key:
                    existing = connection.execute(
                        "SELECT * FROM studio_design_jobs WHERE owner_id = %s AND idempotency_key = %s",
                        (owner_id, idempotency_key),
                    ).fetchone()
                    if existing:
                        if existing["request_fingerprint"] != request_fingerprint:
                            raise IdempotencyConflictError(
                                "Idempotency-Key was already used for a different design submission"
                            )
                        return existing, False
                quota = connection.execute(
                    """
                    INSERT INTO studio_user_quotas (owner_id, max_queued, max_running)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (owner_id) DO UPDATE SET owner_id = EXCLUDED.owner_id
                    RETURNING max_queued, max_running
                    """,
                    (owner_id, default_max_queued, default_max_running),
                ).fetchone()
                # The quota row serializes submissions for one owner. Recheck after
                # acquiring it so concurrent retries cannot consume quota or race the
                # partial unique index.
                if idempotency_key:
                    existing = connection.execute(
                        "SELECT * FROM studio_design_jobs WHERE owner_id = %s AND idempotency_key = %s",
                        (owner_id, idempotency_key),
                    ).fetchone()
                    if existing:
                        if existing["request_fingerprint"] != request_fingerprint:
                            raise IdempotencyConflictError(
                                "Idempotency-Key was already used for a different design submission"
                            )
                        return existing, False
                active = connection.execute(
                    """
                    SELECT count(*) AS count FROM studio_design_jobs
                    WHERE owner_id = %s AND status IN ('queued', 'running')
                    """,
                    (owner_id,),
                ).fetchone()["count"]
                if active >= quota["max_queued"]:
                    raise CapacityExceededError(f"user GPU queue limit ({quota['max_queued']}) reached")
                row = connection.execute(
                    """
                    INSERT INTO studio_design_jobs (
                        id, owner_id, idempotency_key, request_fingerprint, input_name, source_session_id,
                        request, input_object_key, required_capabilities,
                        required_revision, max_attempts
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (owner_id, idempotency_key) WHERE idempotency_key IS NOT NULL
                    DO NOTHING
                    RETURNING *
                    """,
                    (
                        job_id,
                        owner_id,
                        idempotency_key,
                        request_fingerprint,
                        input_name,
                        source_session_id,
                        jsonb(request),
                        input_object_key,
                        jsonb(list(required_capabilities)),
                        required_revision,
                        max_attempts,
                    ),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        "SELECT * FROM studio_design_jobs WHERE owner_id = %s AND idempotency_key = %s",
                        (owner_id, idempotency_key),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("idempotent submission conflict could not be resolved")
                    if row["request_fingerprint"] != request_fingerprint:
                        raise IdempotencyConflictError(
                            "Idempotency-Key was already used for a different design submission"
                        )
                    return row, False
                return row, True

    def get(self, job_id: str, *, owner_id: str | None = None) -> dict[str, Any]:
        query = "SELECT * FROM studio_design_jobs WHERE id = %s"
        parameters: tuple[Any, ...] = (job_id,)
        if owner_id is not None:
            query += " AND owner_id = %s"
            parameters += (owner_id,)
        with self.connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        if row is None:
            raise KeyError(job_id)
        return row

    def cancel(self, job_id: str, *, owner_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                """
                UPDATE studio_design_jobs SET
                    status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END,
                    cancel_requested_at = CASE
                        WHEN status = 'running' THEN COALESCE(cancel_requested_at, now())
                        ELSE cancel_requested_at
                    END,
                    completed_at = CASE WHEN status = 'queued' THEN now() ELSE completed_at END,
                    updated_at = now()
                WHERE id = %s AND owner_id = %s
                RETURNING *
                """,
                (job_id, owner_id),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return row

    def register_worker(
        self,
        worker_id: str,
        *,
        revision: str,
        capabilities: Sequence[str],
        gpu_name: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        _, _, jsonb = _require_psycopg()
        with self.connection() as connection:
            with connection.transaction():
                active = connection.execute(
                    """
                    SELECT 1
                    FROM studio_workers AS worker
                    JOIN studio_design_jobs AS job ON job.id = worker.current_job_id
                    WHERE worker.id = %s AND job.status = 'running' AND job.lease_expires_at >= now()
                    FOR UPDATE OF worker
                    """,
                    (worker_id,),
                ).fetchone()
                if active:
                    raise RuntimeError(f"worker ID {worker_id!r} already owns an active lease")
                connection.execute(
                    """
                    INSERT INTO studio_workers (id, revision, capabilities, gpu_name, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        revision = EXCLUDED.revision,
                        capabilities = EXCLUDED.capabilities,
                        gpu_name = EXCLUDED.gpu_name,
                        metadata = EXCLUDED.metadata,
                        state = 'idle', current_job_id = NULL, heartbeat_at = now()
                    """,
                    (worker_id, revision, jsonb(list(capabilities)), gpu_name, jsonb(metadata or {})),
                )

    def set_quota(
        self,
        owner_id: str,
        *,
        max_queued: int,
        max_running: int,
        scheduling_weight: float,
    ) -> None:
        if (
            max_queued < 1
            or max_running < 1
            or max_running > max_queued
            or not math.isfinite(scheduling_weight)
            or scheduling_weight <= 0
            or scheduling_weight > 1000
        ):
            raise ValueError("quota requires 1 <= max_running <= max_queued and a positive weight")
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO studio_user_quotas (
                    owner_id, max_queued, max_running, scheduling_weight
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (owner_id) DO UPDATE SET
                    max_queued = EXCLUDED.max_queued,
                    max_running = EXCLUDED.max_running,
                    scheduling_weight = EXCLUDED.scheduling_weight,
                    updated_at = now()
                """,
                (owner_id, max_queued, max_running, scheduling_weight),
            )

    def claim(
        self,
        worker_id: str,
        *,
        revision: str,
        capabilities: Sequence[str],
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        _, _, jsonb = _require_psycopg()
        lease_token = uuid.uuid4()
        with self.connection() as connection:
            with connection.transaction():
                worker = connection.execute(
                    "SELECT state, revision, capabilities FROM studio_workers WHERE id = %s FOR UPDATE",
                    (worker_id,),
                ).fetchone()
                if worker is None:
                    raise RuntimeError("worker must register before claiming jobs")
                if worker["state"] != "idle":
                    return None
                if worker["revision"] != revision or set(worker["capabilities"]) != set(capabilities):
                    raise RuntimeError("worker claim identity does not match its registration")
                row = connection.execute(
                    """
                    WITH candidate AS (
                        SELECT job.id
                        FROM studio_design_jobs AS job
                        JOIN studio_user_quotas AS quota ON quota.owner_id = job.owner_id
                        WHERE job.status = 'queued'
                          AND job.attempt < job.max_attempts
                          AND job.required_capabilities <@ %s::jsonb
                          AND (job.required_revision IS NULL OR job.required_revision = %s)
                          AND quota.running_jobs < quota.max_running
                        ORDER BY
                          (quota.running_jobs::double precision / quota.scheduling_weight) ASC,
                          job.priority DESC,
                          job.created_at ASC
                        FOR UPDATE OF job SKIP LOCKED
                        LIMIT 1
                    ), reserved AS (
                        UPDATE studio_user_quotas AS quota SET running_jobs = quota.running_jobs + 1
                        FROM studio_design_jobs AS job, candidate
                        WHERE job.id = candidate.id
                          AND quota.owner_id = job.owner_id
                          AND quota.running_jobs < quota.max_running
                        RETURNING candidate.id
                    )
                    UPDATE studio_design_jobs AS job SET
                        status = 'running',
                        attempt = job.attempt + 1,
                        lease_owner = %s,
                        lease_token = %s,
                        lease_expires_at = now() + make_interval(secs => %s),
                        heartbeat_at = now(),
                        started_at = COALESCE(job.started_at, now()),
                        attempt_started_at = now(),
                        updated_at = now(),
                        phase = 'Starting RFD4'
                    FROM reserved
                    WHERE job.id = reserved.id
                    RETURNING job.*
                    """,
                    (jsonb(list(capabilities)), revision, worker_id, lease_token, lease_seconds),
                ).fetchone()
                connection.execute(
                    """
                    UPDATE studio_workers SET state = %s, current_job_id = %s, heartbeat_at = now()
                    WHERE id = %s
                    """,
                    ("busy" if row else "idle", row["id"] if row else None, worker_id),
                )
                return row

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        *,
        lease_seconds: int,
        phase: str,
    ) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                """
                UPDATE studio_design_jobs SET
                    lease_expires_at = now() + make_interval(secs => %s),
                    heartbeat_at = now(), updated_at = now(), phase = %s
                WHERE id = %s AND status = 'running' AND lease_owner = %s AND lease_token = %s
                RETURNING cancel_requested_at
                """,
                (lease_seconds, phase, job_id, worker_id, lease_token),
            ).fetchone()
            connection.execute(
                "UPDATE studio_workers SET heartbeat_at = now() WHERE id = %s",
                (worker_id,),
            )
        if row is None:
            raise RuntimeError("worker lost its job lease")
        return row["cancel_requested_at"] is not None

    def complete(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        *,
        outputs: list[dict[str, Any]],
        metric_summary: dict[str, Any],
        log_object_key: str | None,
        gpu_seconds: float,
    ) -> None:
        _, _, jsonb = _require_psycopg()
        with self.connection() as connection:
            previous = connection.execute(
                """
                SELECT log_object_key FROM studio_design_jobs
                WHERE id = %s AND status = 'running' AND lease_owner = %s AND lease_token = %s
                FOR UPDATE
                """,
                (job_id, worker_id, lease_token),
            ).fetchone()
            if previous is None:
                raise RuntimeError("worker lost its job lease before completion")
            row = connection.execute(
                """
                UPDATE studio_design_jobs SET
                    status = CASE WHEN cancel_requested_at IS NULL THEN 'completed' ELSE 'cancelled' END,
                    outputs = %s, metric_summary = %s, log_object_key = %s,
                    phase = CASE WHEN cancel_requested_at IS NULL THEN 'Completed' ELSE 'Cancelled' END,
                    error = NULL, gpu_seconds = COALESCE(gpu_seconds, 0) + %s,
                    completed_at = now(), updated_at = now(),
                    attempt_started_at = NULL,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
                WHERE id = %s AND status = 'running' AND lease_owner = %s AND lease_token = %s
                RETURNING id, owner_id
                """,
                (jsonb(outputs), jsonb(metric_summary), log_object_key, gpu_seconds, job_id, worker_id, lease_token),
            ).fetchone()
            if row is None:
                raise RuntimeError("worker lost its job lease before completion")
            previous_log = previous["log_object_key"]
            if previous_log and previous_log != log_object_key:
                _stage_object_deletions(connection, [previous_log])
            connection.execute(
                """
                UPDATE studio_user_quotas SET running_jobs = GREATEST(0, running_jobs - 1)
                WHERE owner_id = %s
                """,
                (row["owner_id"],),
            )
            connection.execute(
                "UPDATE studio_workers SET state = 'idle', current_job_id = NULL, heartbeat_at = now() WHERE id = %s",
                (worker_id,),
            )

    def fail(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        *,
        error: str,
        log_object_key: str | None,
        gpu_seconds: float,
        retryable: bool = True,
        discard_object_keys: Sequence[str] = (),
    ) -> str:
        with self.connection() as connection:
            previous = connection.execute(
                """
                SELECT log_object_key FROM studio_design_jobs
                WHERE id = %s AND status = 'running' AND lease_owner = %s AND lease_token = %s
                FOR UPDATE
                """,
                (job_id, worker_id, lease_token),
            ).fetchone()
            if previous is None:
                raise RuntimeError("worker lost its job lease while reporting failure")
            row = connection.execute(
                """
                UPDATE studio_design_jobs SET
                    status = CASE
                        WHEN cancel_requested_at IS NOT NULL THEN 'cancelled'
                        WHEN %s AND attempt < max_attempts THEN 'queued'
                        ELSE 'failed'
                    END,
                    phase = CASE
                        WHEN cancel_requested_at IS NOT NULL THEN 'Cancelled'
                        WHEN %s AND attempt < max_attempts THEN 'Waiting for GPU'
                        ELSE 'Failed'
                    END,
                    error = CASE
                        WHEN %s AND attempt < max_attempts AND cancel_requested_at IS NULL THEN NULL
                        ELSE %s
                    END,
                    log_object_key = %s, gpu_seconds = COALESCE(gpu_seconds, 0) + %s,
                    completed_at = CASE
                        WHEN cancel_requested_at IS NOT NULL OR NOT %s OR attempt >= max_attempts THEN now()
                        ELSE NULL
                    END,
                    updated_at = now(), attempt_started_at = NULL,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
                WHERE id = %s AND status = 'running' AND lease_owner = %s AND lease_token = %s
                RETURNING status, owner_id
                """,
                (
                    retryable,
                    retryable,
                    retryable,
                    error[:4000],
                    log_object_key,
                    gpu_seconds,
                    retryable,
                    job_id,
                    worker_id,
                    lease_token,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("worker lost its job lease while reporting failure")
            discarded = list(discard_object_keys)
            previous_log = previous["log_object_key"]
            if previous_log and previous_log != log_object_key:
                discarded.append(previous_log)
            _stage_object_deletions(connection, discarded)
            connection.execute(
                """
                UPDATE studio_user_quotas SET running_jobs = GREATEST(0, running_jobs - 1)
                WHERE owner_id = %s
                """,
                (row["owner_id"],),
            )
            connection.execute(
                "UPDATE studio_workers SET state = 'idle', current_job_id = NULL, heartbeat_at = now() WHERE id = %s",
                (worker_id,),
            )
        return row["status"]

    def recover_expired_leases(self) -> int:
        with self.connection() as connection:
            recovered = connection.execute(
                """
                UPDATE studio_design_jobs SET
                    status = CASE
                        WHEN cancel_requested_at IS NOT NULL THEN 'cancelled'
                        WHEN attempt < max_attempts THEN 'queued'
                        ELSE 'failed'
                    END,
                    phase = CASE
                        WHEN cancel_requested_at IS NOT NULL THEN 'Cancelled'
                        WHEN attempt < max_attempts THEN 'Waiting for GPU'
                        ELSE 'Failed'
                    END,
                    error = CASE
                        WHEN cancel_requested_at IS NULL AND attempt >= max_attempts
                        THEN 'worker lease expired after the final attempt'
                        ELSE error
                    END,
                    completed_at = CASE
                        WHEN cancel_requested_at IS NOT NULL OR attempt >= max_attempts THEN now()
                        ELSE NULL
                    END,
                    gpu_seconds = COALESCE(gpu_seconds, 0) + GREATEST(
                        0,
                        EXTRACT(EPOCH FROM (COALESCE(heartbeat_at, now()) - COALESCE(attempt_started_at, now())))
                    ),
                    updated_at = now(), attempt_started_at = NULL,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
                WHERE status = 'running' AND lease_expires_at < now()
                """
            ).rowcount
            connection.execute(
                """
                UPDATE studio_user_quotas AS quota SET running_jobs = (
                    SELECT count(*) FROM studio_design_jobs AS job
                    WHERE job.owner_id = quota.owner_id AND job.status = 'running'
                )
                """
            )
            connection.execute(
                """
                UPDATE studio_workers AS worker SET state = 'idle', current_job_id = NULL
                WHERE worker.current_job_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM studio_design_jobs AS job
                    WHERE job.id = worker.current_job_id
                      AND job.status = 'running'
                      AND job.lease_expires_at >= now()
                )
                """
            )
        return recovered

    def operational_metrics(self) -> dict[str, float]:
        with self.connection() as connection:
            jobs = connection.execute(
                """
                SELECT
                    count(*) FILTER (WHERE status = 'queued') AS queued,
                    count(*) FILTER (WHERE status = 'running') AS running,
                    count(*) FILTER (WHERE status = 'failed') AS failed,
                    COALESCE(
                        EXTRACT(EPOCH FROM (now() - min(created_at) FILTER (WHERE status = 'queued'))),
                        0
                    ) AS oldest_queue_seconds
                FROM studio_design_jobs
                """
            ).fetchone()
            workers = connection.execute(
                """
                SELECT
                    count(*) FILTER (WHERE heartbeat_at >= now() - interval '2 minutes') AS healthy,
                    count(*) FILTER (
                        WHERE heartbeat_at >= now() - interval '2 minutes' AND state = 'busy'
                    ) AS busy
                FROM studio_workers
                """
            ).fetchone()
            usage = connection.execute(
                "SELECT COALESCE(sum(gpu_seconds), 0) AS gpu_seconds FROM studio_usage_ledger"
            ).fetchone()
        return {
            "jobs_queued": float(jobs["queued"]),
            "jobs_running": float(jobs["running"]),
            "jobs_failed": float(jobs["failed"]),
            "oldest_queue_seconds": float(jobs["oldest_queue_seconds"]),
            "gpu_seconds_total": float(usage["gpu_seconds"]),
            "workers_healthy": float(workers["healthy"]),
            "workers_busy": float(workers["busy"]),
        }

    def stage_object_deletions(self, keys: Sequence[str]) -> None:
        """Add unreferenced attempt artifacts to the durable cleanup outbox."""

        with self.connection() as connection:
            _stage_object_deletions(connection, keys)

    def delete_expired(self, *, retention_seconds: int) -> list[str]:
        """Stage durable blob deletion while pruning expired session/job rows."""

        with self.connection() as connection:
            with connection.transaction():
                connection.execute("DELETE FROM studio_sessions WHERE expires_at < now()")
                rows = connection.execute(
                    """
                    DELETE FROM studio_design_jobs
                    WHERE status IN ('completed', 'failed', 'cancelled')
                      AND completed_at < now() - make_interval(secs => %s)
                    RETURNING input_object_key, log_object_key, outputs
                    """,
                    (retention_seconds,),
                ).fetchall()
                keys: list[str] = []
                for row in rows:
                    keys.append(row["input_object_key"])
                    if row["log_object_key"]:
                        keys.append(row["log_object_key"])
                    keys.extend(
                        output["object_key"]
                        for output in row["outputs"]
                        if isinstance(output, dict) and isinstance(output.get("object_key"), str)
                    )
                _stage_object_deletions(connection, keys)
                pending = connection.execute(
                    """
                    SELECT object_key FROM studio_pending_object_deletions
                    ORDER BY updated_at, created_at LIMIT 1000
                    """
                ).fetchall()
        return [row["object_key"] for row in pending]

    def acknowledge_object_deletion(self, key: str) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM studio_pending_object_deletions WHERE object_key = %s", (key,))

    def record_object_deletion_failure(self, key: str, error: str) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE studio_pending_object_deletions SET
                    attempts = attempts + 1, last_error = %s, updated_at = now()
                WHERE object_key = %s
                """,
                (error[:1000], key),
            )

    def close(self) -> None:
        self.pool.close()


class DurableDesignProvider:
    """API-side durable provider; it never imports or executes the GPU model."""

    def __init__(
        self,
        repository: PostgresJobRepository,
        object_store: ObjectStore,
        *,
        required_revision: str | None,
        available_capabilities: Sequence[str] = ("rfd4",),
        default_max_queued: int = 8,
        default_max_running: int = 1,
        max_attempts: int = 2,
    ) -> None:
        if default_max_running > default_max_queued:
            raise ValueError("default running quota cannot exceed the queued-job quota")
        self.repository = repository
        self.object_store = object_store
        self.required_revision = required_revision
        self.available_capabilities = frozenset(item.strip() for item in available_capabilities if item.strip())
        if "rfd4" not in self.available_capabilities:
            raise ValueError("durable deployments must advertise the rfd4 capability")
        self.default_max_queued = max(1, default_max_queued)
        self.default_max_running = max(1, default_max_running)
        self.max_attempts = max(1, min(max_attempts, 10))

    @classmethod
    def from_environment(cls) -> DurableDesignProvider:
        dsn = os.environ["ANNOTATION_STUDIO_DATABASE_URL"]
        object_store_url = os.environ["ANNOTATION_STUDIO_OBJECT_STORE_URL"]
        return cls(
            PostgresJobRepository(dsn),
            object_store_from_url(object_store_url),
            required_revision=os.environ.get("ANNOTATION_STUDIO_REQUIRED_WORKER_REVISION"),
            available_capabilities=os.environ.get("ANNOTATION_STUDIO_AVAILABLE_CAPABILITIES", "rfd4").split(","),
            default_max_queued=int(os.environ.get("ANNOTATION_STUDIO_DEFAULT_MAX_QUEUED", "8")),
            default_max_running=int(os.environ.get("ANNOTATION_STUDIO_DEFAULT_MAX_RUNNING", "1")),
            max_attempts=int(os.environ.get("ANNOTATION_STUDIO_JOB_MAX_ATTEMPTS", "2")),
        )

    @property
    def enabled(self) -> bool:
        return True

    def schema(self) -> dict[str, Any]:
        defaults = DesignJobRequest().model_dump()
        refolding_oracles = [oracle for oracle in ("of3", "af3") if oracle in self.available_capabilities]
        if refolding_oracles:
            defaults["refolding_oracle"] = refolding_oracles[0]
        defaults["search"] = SearchOptions(step_checkpoints=[0, 50, 100]).model_dump()
        return {
            "enabled": True,
            "provider": "durable-rfd4",
            "capabilities": {
                "durable": True,
                "refolding_oracles": refolding_oracles,
                "default_refolding_oracle": refolding_oracles[0] if refolding_oracles else None,
            },
            "defaults": defaults,
            "limits": {
                "num_designs": 64,
                "batch_size": 32,
                "steps": 1000,
                "queued_per_user": self.default_max_queued,
                "running_per_user": self.default_max_running,
            },
            "metrics_tags": [
                "design",
                "unindexed",
                "3d_coordinate_conditioning",
                "hotspot_conditioning",
                "interchain_interactions",
                "rasa_conditioning",
                *(sorted(REFOLDING_METRIC_TAGS) if refolding_oracles else []),
            ],
        }

    def submit(
        self,
        cif: str,
        name: str,
        request: DesignJobRequest,
        *,
        source_session_id: str | None = None,
        owner_id: str = "local",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4()
        input_key = f"jobs/{job_id}/input.cif"
        request_payload = request.model_dump()
        fingerprint = design_request_fingerprint(cif, name, request)
        # Reject deterministic request/config errors before they consume queue or
        # object-store capacity. Workers replace every placeholder path below.
        build_rfd4_config(
            request,
            input_path=Path("/job/input.cif"),
            output_dir=Path("/job/outputs"),
            run_name=str(job_id),
            project_root=Path("/runtime/rfd4"),
        )
        capabilities = ["rfd4"]
        if request_uses_refolding(request):
            capabilities.append(request.refolding_oracle)
        missing_capabilities = set(capabilities) - self.available_capabilities
        if missing_capabilities:
            raise RuntimeError(
                "no worker pool is configured for required capabilities: " + ", ".join(sorted(missing_capabilities))
            )
        self.object_store.put(input_key, cif.encode(), content_type="chemical/x-mmcif")
        try:
            row, created = self.repository.create(
                job_id=job_id,
                owner_id=owner_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                input_name=name,
                source_session_id=source_session_id,
                request=request_payload,
                input_object_key=input_key,
                required_capabilities=capabilities,
                required_revision=self.required_revision,
                default_max_queued=self.default_max_queued,
                default_max_running=self.default_max_running,
                max_attempts=self.max_attempts,
            )
        except Exception:
            self.object_store.delete(input_key)
            raise
        if not created:
            self.object_store.delete(input_key)
        return self._response(row)

    def _response(self, row: dict[str, Any]) -> dict[str, Any]:
        result = _record(row)
        log_key = row.get("log_object_key")
        log_tail = ""
        if log_key:
            try:
                log_tail = self.object_store.get(log_key).decode("utf-8", errors="replace")[-4000:]
            except Exception:
                log_tail = ""
        result["log_tail"] = ANSI_ESCAPE.sub("", log_tail)
        status = result["status"]
        stop = time.time() if status in {"queued", "running"} else result["updated_at"]
        result["progress"] = {
            "phase": row["phase"],
            "elapsed_seconds": max(0, round(stop - result["created_at"])),
            "log_updated_at": result.get("heartbeat_at"),
            "indeterminate": status in {"queued", "running"},
        }
        return result

    def get(self, job_id: str, *, owner_id: str = "local") -> dict[str, Any]:
        return self._response(self.repository.get(job_id, owner_id=owner_id))

    def cancel(self, job_id: str, *, owner_id: str = "local") -> dict[str, Any]:
        return self._response(self.repository.cancel(job_id, owner_id=owner_id))

    def output_cif(
        self,
        job_id: str,
        output_id: str,
        *,
        owner_id: str = "local",
    ) -> tuple[str, dict[str, Any]]:
        row = self.repository.get(job_id, owner_id=owner_id)
        output = next((item for item in row["outputs"] if item["id"] == output_id), None)
        if output is None:
            raise KeyError(output_id)
        return self.object_store.get(output["object_key"]).decode(), copy.deepcopy(output)

    def input_cif(self, job_id: str, *, owner_id: str = "local") -> str:
        row = self.repository.get(job_id, owner_id=owner_id)
        return self.object_store.get(row["input_object_key"]).decode()

    def operational_metrics(self) -> dict[str, float]:
        return self.repository.operational_metrics()

    def healthcheck(self) -> None:
        with self.repository.connection() as connection:
            connection.execute("SELECT 1 FROM studio_design_jobs LIMIT 1")

    def close(self) -> None:
        self.repository.close()


class DurableWorker:
    """One-worker-per-GPU lease consumer with bounded retries and cancellation."""

    def __init__(
        self,
        repository: PostgresJobRepository,
        object_store: ObjectStore,
        *,
        worker_id: str,
        revision: str,
        capabilities: Sequence[str],
        project_root: Path,
        command: Sequence[str],
        staging_root: Path,
        gpu_name: str | None = None,
        lease_seconds: int = 90,
        heartbeat_seconds: int = 20,
        timeout_seconds: int = 4 * 60 * 60,
    ) -> None:
        if heartbeat_seconds < 1 or lease_seconds < 3 or heartbeat_seconds >= lease_seconds / 2:
            raise ValueError("heartbeat interval must be less than half the lease duration")
        if timeout_seconds < 1:
            raise ValueError("job timeout must be positive")
        if not command:
            raise ValueError("worker command cannot be empty")
        self.repository = repository
        self.object_store = object_store
        self.worker_id = worker_id
        self.revision = revision
        self.capabilities = tuple(sorted({item.strip() for item in capabilities if item.strip()}))
        if "rfd4" not in self.capabilities:
            raise ValueError("workers must advertise the rfd4 capability")
        self.project_root = project_root.resolve()
        if not self.project_root.is_dir():
            raise ValueError(f"RFD4 project root does not exist: {self.project_root}")
        self.command = tuple(command)
        self.staging_root = staging_root.resolve()
        self.gpu_name = gpu_name
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._stop_requested = False
        self.repository.register_worker(
            worker_id,
            revision=revision,
            capabilities=self.capabilities,
            gpu_name=gpu_name,
        )

    def stop(self) -> None:
        """Request graceful shutdown and terminate only this worker's child."""

        self._stop_requested = True
        if self._process is not None and self._process.poll() is None:
            _signal_process(self._process, signal.SIGTERM)

    def run_once(self) -> bool:
        self.repository.recover_expired_leases()
        row = self.repository.claim(
            self.worker_id,
            revision=self.revision,
            capabilities=self.capabilities,
            lease_seconds=self.lease_seconds,
        )
        if row is None:
            return False
        self._run(row)
        return True

    def _run(self, row: dict[str, Any]) -> None:
        job_id = str(row["id"])
        attempt = int(row["attempt"])
        lease_token = str(row["lease_token"])
        job_dir = self.staging_root / job_id
        input_path = job_dir / "input.cif"
        output_dir = job_dir / "outputs"
        log_path = job_dir / "job.log"
        started = time.monotonic()
        log_key: str | None = None
        process: subprocess.Popen[str] | None = None
        published_keys: list[str] = []
        try:
            if job_dir.exists():
                shutil.rmtree(job_dir)
            job_dir.mkdir(parents=True, mode=0o700)
            input_path.write_bytes(self.object_store.get(row["input_object_key"]))
            if self.repository.heartbeat(
                job_id,
                self.worker_id,
                lease_token,
                lease_seconds=self.lease_seconds,
                phase="Preparing input",
            ):
                raise RuntimeError("job cancelled")
            request = DesignJobRequest.model_validate(row["request"])
            config = build_rfd4_config(
                request,
                input_path=input_path,
                output_dir=output_dir,
                run_name=job_id,
                project_root=self.project_root,
            )
            (job_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
            command = [*self.command, "--config-path", str(job_dir), "--config-name", "config"]
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    command,
                    cwd=self.project_root,
                    env=worker_child_environment(self.project_root),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=os.name == "posix",
                )
                self._process = process
                deadline = started + self.timeout_seconds
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        _signal_process(process, signal.SIGKILL)
                        raise RuntimeError(f"RFD4 exceeded its {self.timeout_seconds}s timeout")
                    if self._stop_requested:
                        raise RuntimeError("worker is shutting down")
                    cancelled = self.repository.heartbeat(
                        job_id,
                        self.worker_id,
                        lease_token,
                        lease_seconds=self.lease_seconds,
                        phase="Sampling designs",
                    )
                    if cancelled:
                        _signal_process(process, signal.SIGTERM)
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            _signal_process(process, signal.SIGKILL)
                        raise RuntimeError("job cancelled")
                    try:
                        process.wait(timeout=self.heartbeat_seconds)
                    except subprocess.TimeoutExpired:
                        continue
            if process.returncode != 0:
                raise RuntimeError(f"RFD4 exited with status {process.returncode}")
            if self.repository.heartbeat(
                job_id,
                self.worker_id,
                lease_token,
                lease_seconds=self.lease_seconds,
                phase="Collecting outputs",
            ):
                raise RuntimeError("job cancelled")
            outputs = collect_outputs(output_dir)
            if not outputs:
                raise RuntimeError("RFD4 completed without generated CIF outputs")
            for output in outputs:
                if self._stop_requested:
                    raise RuntimeError("worker is shutting down")
                source = Path(output.pop("cif_path"))
                object_key = f"jobs/{job_id}/attempt-{attempt}/outputs/{output['id']}.cif"
                self.object_store.put(object_key, source.read_bytes(), content_type="chemical/x-mmcif")
                published_keys.append(object_key)
                output["object_key"] = object_key
                if self.repository.heartbeat(
                    job_id,
                    self.worker_id,
                    lease_token,
                    lease_seconds=self.lease_seconds,
                    phase="Publishing outputs",
                ):
                    raise RuntimeError("job cancelled")
            try:
                log_key = self._upload_log(job_id, attempt, log_path)
            except Exception:
                log_key = None
            self.repository.complete(
                job_id,
                self.worker_id,
                lease_token,
                outputs=outputs,
                metric_summary=summarize_metrics(outputs),
                log_object_key=log_key,
                gpu_seconds=time.monotonic() - started,
            )
        except Exception as error:
            if process is not None and process.poll() is None:
                _signal_process(process, signal.SIGKILL)
                process.wait()
            failure = str(error)
            if log_path.exists():
                try:
                    log_key = self._upload_log(job_id, attempt, log_path)
                except Exception as log_error:
                    log_key = None
                    failure = f"{failure}; log upload failed: {log_error}"
            retryable = str(error) != "job cancelled"
            try:
                self.repository.fail(
                    job_id,
                    self.worker_id,
                    lease_token,
                    error=failure,
                    log_object_key=log_key,
                    gpu_seconds=time.monotonic() - started,
                    retryable=retryable,
                    discard_object_keys=published_keys,
                )
            except Exception:
                # If completion committed but its response was lost, keep every
                # artifact referenced by the canonical record. Otherwise queue
                # this fenced attempt's objects for later deletion.
                try:
                    current = self.repository.get(job_id)
                    retained = {
                        current.get("input_object_key"),
                        current.get("log_object_key"),
                        *(
                            output.get("object_key")
                            for output in current.get("outputs", [])
                            if isinstance(output, dict)
                        ),
                    }
                    self.repository.stage_object_deletions(
                        [key for key in [*published_keys, log_key] if key is not None and key not in retained]
                    )
                except Exception:
                    pass
                raise
        finally:
            self._process = None
            shutil.rmtree(job_dir, ignore_errors=True)

    def _upload_log(self, job_id: str, attempt: int, log_path: Path) -> str:
        key = f"jobs/{job_id}/attempt-{attempt}/job.log"
        self.object_store.put(key, log_path.read_bytes(), content_type="text/plain; charset=utf-8")
        return key
