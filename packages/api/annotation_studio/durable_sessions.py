"""Transactional, owner-scoped Studio documents for horizontally scaled APIs."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

from .contracts import CapacityExceededError, ConditionProvider, SessionNotFoundError, ValidationProvider
from .core import StudioDocument
from .durable_jobs import PostgresJobRepository, _require_psycopg
from .identity import current_owner


class PostgresDocumentStore:
    """Shared session state with row locks and cross-replica undo preservation."""

    def __init__(
        self,
        repository: PostgresJobRepository,
        *,
        condition_provider: ConditionProvider,
        validation_provider: ValidationProvider,
        ttl_seconds: int = 2 * 60 * 60,
        max_sessions_per_user: int = 64,
    ) -> None:
        self.repository = repository
        self.condition_provider = condition_provider
        self.validation_provider = validation_provider
        self.ttl_seconds = max(60, ttl_seconds)
        self.max_sessions_per_user = max(1, max_sessions_per_user)

    @property
    def persistent(self) -> bool:
        return True

    def _document(self, row: dict[str, Any]) -> StudioDocument:
        return StudioDocument.from_state(
            {
                "name": row["name"],
                "cif": row["cif"],
                "undo_cifs": row["undo_cifs"],
                "revision": row["revision"],
            },
            condition_provider=self.condition_provider,
            validation_provider=self.validation_provider,
        )

    def add(self, document: StudioDocument) -> str:
        _, _, jsonb = _require_psycopg()
        state = document.export_state()
        owner_id = current_owner()
        session_id = secrets.token_urlsafe(24)
        with self.repository.connection() as connection:
            with connection.transaction():
                connection.execute("DELETE FROM studio_sessions WHERE expires_at < now()")
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 1))",
                    (owner_id,),
                )
                count = connection.execute(
                    "SELECT count(*) AS count FROM studio_sessions WHERE owner_id = %s",
                    (owner_id,),
                ).fetchone()["count"]
                if count >= self.max_sessions_per_user:
                    raise CapacityExceededError(f"user session limit ({self.max_sessions_per_user}) reached")
                connection.execute(
                    """
                    INSERT INTO studio_sessions (
                        id, owner_id, name, cif, undo_cifs, revision, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, now() + make_interval(secs => %s))
                    """,
                    (
                        session_id,
                        owner_id,
                        state["name"],
                        state["cif"],
                        jsonb(state["undo_cifs"]),
                        state["revision"],
                        self.ttl_seconds,
                    ),
                )
        return session_id

    def get(self, session_id: str) -> StudioDocument:
        owner_id = current_owner()
        with self.repository.connection() as connection:
            row = connection.execute(
                """
                UPDATE studio_sessions SET
                    updated_at = now(), expires_at = now() + make_interval(secs => %s)
                WHERE id = %s AND owner_id = %s AND expires_at >= now()
                RETURNING *
                """,
                (self.ttl_seconds, session_id, owner_id),
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return self._document(row)

    def call[T](
        self,
        session_id: str,
        operation: Callable[[StudioDocument], T],
        *,
        persist: bool = False,
    ) -> T:
        _, _, jsonb = _require_psycopg()
        owner_id = current_owner()
        with self.repository.connection() as connection:
            with connection.transaction():
                suffix = " FOR UPDATE" if persist else ""
                row = connection.execute(
                    """
                    SELECT * FROM studio_sessions
                    WHERE id = %s AND owner_id = %s AND expires_at >= now()
                    """
                    + suffix,
                    (session_id, owner_id),
                ).fetchone()
                if row is None:
                    raise SessionNotFoundError(session_id)
                document = self._document(row)
                result = operation(document)
                if persist:
                    state = document.export_state()
                    connection.execute(
                        """
                        UPDATE studio_sessions SET
                            name = %s, cif = %s, undo_cifs = %s, revision = %s,
                            updated_at = now(), expires_at = now() + make_interval(secs => %s)
                        WHERE id = %s
                        """,
                        (
                            state["name"],
                            state["cif"],
                            jsonb(state["undo_cifs"]),
                            state["revision"],
                            self.ttl_seconds,
                            session_id,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE studio_sessions SET
                            updated_at = now(), expires_at = now() + make_interval(secs => %s)
                        WHERE id = %s
                        """,
                        (self.ttl_seconds, session_id),
                    )
                return result
