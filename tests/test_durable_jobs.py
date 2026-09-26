from __future__ import annotations

import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest
from annotation_studio.contracts import (
    NEUTRAL_CONDITION_PROVIDER,
    NEUTRAL_VALIDATION_PROVIDER,
    CapacityExceededError,
)
from annotation_studio.core import StudioDocument
from annotation_studio.design_jobs import DesignJobRequest, IdempotencyConflictError
from annotation_studio.durable_jobs import (
    DurableDesignProvider,
    DurableWorker,
    LocalObjectStore,
    PostgresJobRepository,
    worker_child_environment,
)
from annotation_studio.durable_sessions import PostgresDocumentStore
from annotation_studio.identity import reset_current_owner, set_current_owner

FIXTURE = Path(__file__).parent / "fixtures" / "SAM_hbonds.cif"
FAKE_RFD4 = Path(__file__).parent / "fake_rfd4.py"
TEST_DATABASE_URL = os.environ.get("ANNOTATION_STUDIO_TEST_DATABASE_URL")


def test_worker_child_environment_is_allowlisted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", "/runtime/bin")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2")
    monkeypatch.setenv("CUSTOM_MODEL_CACHE", "/cache")
    monkeypatch.setenv("ANNOTATION_STUDIO_WORKER_CHILD_ENV_ALLOWLIST", "CUSTOM_MODEL_CACHE")
    monkeypatch.setenv("ANNOTATION_STUDIO_DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    environment = worker_child_environment(tmp_path)
    assert environment["PATH"] == "/runtime/bin"
    assert environment["CUDA_VISIBLE_DEVICES"] == "2"
    assert environment["CUSTOM_MODEL_CACHE"] == "/cache"
    assert environment["PROJECT_ROOT"] == str(tmp_path)
    assert "ANNOTATION_STUDIO_DATABASE_URL" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    monkeypatch.setenv("ANNOTATION_STUDIO_WORKER_CHILD_ENV_ALLOWLIST", "aws_secret_access_key")
    with pytest.raises(ValueError, match="unsafe worker child environment name"):
        worker_child_environment(tmp_path)


def test_local_object_store_confines_keys_and_round_trips(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    store.put("jobs/one/input.cif", b"data", content_type="chemical/x-mmcif")
    assert store.get("jobs/one/input.cif") == b"data"
    with pytest.raises(ValueError, match="escaped"):
        store.put("../outside", b"bad", content_type="text/plain")
    store.delete("jobs/one/input.cif")
    with pytest.raises(FileNotFoundError):
        store.get("jobs/one/input.cif")


def test_document_state_preserves_undo_across_serialization() -> None:
    document = StudioDocument.from_cif(FIXTURE.read_text(), "input")
    original_charge = int(document.array.charge[0])
    document.set_charge([0], value=original_charge + 1)
    restored = StudioDocument.from_state(document.export_state())
    assert restored.summary()["undo_depth"] == 1
    assert restored.undo() is True
    assert int(restored.array.charge[0]) == original_charge


def test_durable_provider_serves_the_generic_job_routes(tmp_path: Path) -> None:
    # /api/providers/rfd4/... calls parse_request, artifact and the schema label; none of them needs the database.
    provider = DurableDesignProvider(
        cast(PostgresJobRepository, None),
        LocalObjectStore(tmp_path / "objects"),
        required_revision=None,
    )
    assert provider.schema()["label"] == "RFD4-Proteina"
    assert provider.parse_request({"num_designs": 2}) == DesignJobRequest(num_designs=2)
    with pytest.raises(KeyError):
        provider.artifact("job", "output", "result", owner_id="alice@example.org")


@pytest.fixture
def repository() -> PostgresJobRepository:
    if not TEST_DATABASE_URL:
        pytest.skip("ANNOTATION_STUDIO_TEST_DATABASE_URL is not configured")
    result = PostgresJobRepository(TEST_DATABASE_URL)
    result.migrate()
    with result.connection() as connection:
        connection.execute(
            "TRUNCATE studio_workers, studio_design_jobs, studio_user_quotas, studio_sessions, "
            "studio_job_events, studio_usage_ledger, studio_pending_object_deletions CASCADE"
        )
    try:
        yield result
    finally:
        result.close()


def _provider(repository: PostgresJobRepository, tmp_path: Path) -> DurableDesignProvider:
    return DurableDesignProvider(
        repository,
        LocalObjectStore(tmp_path / "objects"),
        required_revision="test-revision",
        available_capabilities=("rfd4", "of3", "af3"),
        default_max_queued=2,
        default_max_running=1,
    )


def test_durable_jobs_are_idempotent_and_owner_scoped(
    repository: PostgresJobRepository,
    tmp_path: Path,
) -> None:
    provider = _provider(repository, tmp_path)
    request = DesignJobRequest(num_designs=1)
    first = provider.submit(
        FIXTURE.read_text(),
        "input",
        request,
        owner_id="alice@example.org",
        idempotency_key="submission-1",
    )
    duplicate = provider.submit(
        FIXTURE.read_text(),
        "input",
        request,
        owner_id="alice@example.org",
        idempotency_key="submission-1",
    )
    assert duplicate["id"] == first["id"]
    assert first["owner_id"] == "alice@example.org"
    with pytest.raises(IdempotencyConflictError):
        provider.submit(
            FIXTURE.read_text(),
            "input",
            DesignJobRequest(num_designs=2),
            owner_id="alice@example.org",
            idempotency_key="submission-1",
        )
    with pytest.raises(KeyError):
        provider.get(first["id"], owner_id="bob@example.org")
    assert provider.get(first["id"], owner_id="alice@example.org")["status"] == "queued"


def test_claims_enforce_capabilities_running_quota_and_cancellation(
    repository: PostgresJobRepository,
    tmp_path: Path,
) -> None:
    provider = _provider(repository, tmp_path)
    regular = provider.submit(FIXTURE.read_text(), "regular", DesignJobRequest(), owner_id="alice")
    of3 = provider.submit(
        FIXTURE.read_text(),
        "oracle",
        DesignJobRequest(metrics_tags=["unconditional_refolding"], refolding_oracle="of3"),
        owner_id="alice",
    )
    repository.register_worker("rfd4-worker", revision="test-revision", capabilities=["rfd4"], gpu_name=None)
    claimed = repository.claim(
        "rfd4-worker",
        revision="test-revision",
        capabilities=["rfd4"],
        lease_seconds=60,
    )
    assert str(claimed["id"]) == regular["id"]
    assert (
        repository.claim(
            "rfd4-worker",
            revision="test-revision",
            capabilities=["rfd4"],
            lease_seconds=60,
        )
        is None
    )
    cancelled = provider.cancel(regular["id"], owner_id="alice")
    assert cancelled["status"] == "running"
    with pytest.raises(RuntimeError, match="lost its job lease"):
        repository.heartbeat(
            regular["id"],
            "rfd4-worker",
            str(uuid.uuid4()),
            lease_seconds=60,
            phase="stale worker",
        )
    assert (
        repository.heartbeat(
            regular["id"],
            "rfd4-worker",
            str(claimed["lease_token"]),
            lease_seconds=60,
            phase="Sampling designs",
        )
        is True
    )
    repository.fail(
        regular["id"],
        "rfd4-worker",
        str(claimed["lease_token"]),
        error="job cancelled",
        log_object_key=None,
        gpu_seconds=1,
        retryable=False,
    )
    assert provider.get(regular["id"], owner_id="alice")["status"] == "cancelled"

    repository.register_worker(
        "of3-worker",
        revision="test-revision",
        capabilities=["rfd4", "of3"],
        gpu_name=None,
    )
    claimed = repository.claim(
        "of3-worker",
        revision="test-revision",
        capabilities=["rfd4", "of3"],
        lease_seconds=60,
    )
    assert str(claimed["id"]) == of3["id"]


def test_claim_rejects_worker_identity_drift(repository: PostgresJobRepository) -> None:
    repository.register_worker(
        "worker",
        revision="test-revision",
        capabilities=["rfd4"],
        gpu_name=None,
    )
    with pytest.raises(RuntimeError, match="identity does not match"):
        repository.claim(
            "worker",
            revision="different-revision",
            capabilities=["rfd4", "af3"],
            lease_seconds=60,
        )


def test_expired_lease_is_requeued_and_accounts_elapsed_gpu_time(
    repository: PostgresJobRepository,
    tmp_path: Path,
) -> None:
    provider = _provider(repository, tmp_path)
    submitted = provider.submit(FIXTURE.read_text(), "input", DesignJobRequest(), owner_id="alice")
    repository.register_worker(
        "worker",
        revision="test-revision",
        capabilities=["rfd4"],
        gpu_name=None,
    )
    claimed = repository.claim(
        "worker",
        revision="test-revision",
        capabilities=["rfd4"],
        lease_seconds=60,
    )
    assert claimed is not None
    with repository.connection() as connection:
        connection.execute(
            """
            UPDATE studio_design_jobs SET
                attempt_started_at = now() - interval '25 seconds',
                heartbeat_at = now() - interval '5 seconds',
                lease_expires_at = now() - interval '1 second'
            WHERE id = %s
            """,
            (submitted["id"],),
        )
    assert repository.recover_expired_leases() == 1
    recovered = repository.get(submitted["id"], owner_id="alice")
    assert recovered["status"] == "queued"
    assert 19 <= recovered["gpu_seconds"] <= 21
    with repository.connection() as connection:
        quota = connection.execute("SELECT running_jobs FROM studio_user_quotas WHERE owner_id = 'alice'").fetchone()
        worker = connection.execute("SELECT state, current_job_id FROM studio_workers WHERE id = 'worker'").fetchone()
    assert quota["running_jobs"] == 0
    assert worker == {"state": "idle", "current_job_id": None}


def test_failed_attempt_artifacts_enter_cleanup_outbox(
    repository: PostgresJobRepository,
    tmp_path: Path,
) -> None:
    provider = _provider(repository, tmp_path)
    submitted = provider.submit(FIXTURE.read_text(), "input", DesignJobRequest(), owner_id="alice")
    repository.register_worker(
        "worker",
        revision="test-revision",
        capabilities=["rfd4"],
        gpu_name=None,
    )
    first = repository.claim(
        "worker",
        revision="test-revision",
        capabilities=["rfd4"],
        lease_seconds=60,
    )
    assert first is not None
    assert (
        repository.fail(
            submitted["id"],
            "worker",
            str(first["lease_token"]),
            error="transient",
            log_object_key="jobs/job/attempt-1/job.log",
            gpu_seconds=1,
            discard_object_keys=["jobs/job/attempt-1/output.cif"],
        )
        == "queued"
    )
    second = repository.claim(
        "worker",
        revision="test-revision",
        capabilities=["rfd4"],
        lease_seconds=60,
    )
    assert second is not None
    repository.fail(
        submitted["id"],
        "worker",
        str(second["lease_token"]),
        error="terminal",
        log_object_key="jobs/job/attempt-2/job.log",
        gpu_seconds=1,
        retryable=False,
        discard_object_keys=["jobs/job/attempt-2/output.cif"],
    )
    with repository.connection() as connection:
        pending = {
            row["object_key"]
            for row in connection.execute("SELECT object_key FROM studio_pending_object_deletions").fetchall()
        }
    assert pending == {
        "jobs/job/attempt-1/job.log",
        "jobs/job/attempt-1/output.cif",
        "jobs/job/attempt-2/output.cif",
    }


def test_worker_executes_and_publishes_outputs(
    repository: PostgresJobRepository,
    tmp_path: Path,
) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    provider = DurableDesignProvider(
        repository,
        store,
        required_revision="test-revision",
        available_capabilities=("rfd4",),
    )
    submitted = provider.submit(
        FIXTURE.read_text(),
        "input",
        DesignJobRequest(num_designs=1),
        owner_id="alice",
    )
    worker = DurableWorker(
        repository,
        store,
        worker_id="worker-0",
        revision="test-revision",
        capabilities=["rfd4"],
        project_root=tmp_path,
        command=[sys.executable, str(FAKE_RFD4)],
        staging_root=tmp_path / "staging",
        lease_seconds=10,
        heartbeat_seconds=1,
        timeout_seconds=30,
    )
    assert worker.run_once() is True
    completed = provider.get(submitted["id"], owner_id="alice")
    assert completed["status"] == "completed", completed["log_tail"]
    assert completed["gpu_seconds"] >= 0
    cif, output = provider.output_cif(
        submitted["id"],
        completed["outputs"][0]["id"],
        owner_id="alice",
    )
    assert "_atom_site." in cif
    assert "object_key" in output
    with repository.connection() as connection:
        usage = connection.execute(
            "SELECT gpu_seconds FROM studio_usage_ledger WHERE job_id = %s",
            (submitted["id"],),
        ).fetchone()
        connection.execute(
            "UPDATE studio_design_jobs SET completed_at = now() - interval '8 days' WHERE id = %s",
            (submitted["id"],),
        )
    assert usage["gpu_seconds"] >= 0
    deletion_keys = repository.delete_expired(retention_seconds=7 * 24 * 60 * 60)
    assert f"jobs/{submitted['id']}/input.cif" in deletion_keys
    with pytest.raises(KeyError):
        provider.get(submitted["id"], owner_id="alice")
    with repository.connection() as connection:
        assert connection.execute(
            "SELECT 1 FROM studio_usage_ledger WHERE job_id = %s",
            (submitted["id"],),
        ).fetchone()
        assert connection.execute(
            "SELECT 1 FROM studio_job_events WHERE job_id = %s",
            (submitted["id"],),
        ).fetchone()


def test_shared_sessions_are_owner_scoped_and_preserve_undo(
    repository: PostgresJobRepository,
) -> None:
    store = PostgresDocumentStore(
        repository,
        condition_provider=NEUTRAL_CONDITION_PROVIDER,
        validation_provider=NEUTRAL_VALIDATION_PROVIDER,
    )
    token = set_current_owner("alice")
    try:
        document = StudioDocument.from_cif(
            FIXTURE.read_text(),
            "input",
            condition_provider=NEUTRAL_CONDITION_PROVIDER,
            validation_provider=NEUTRAL_VALIDATION_PROVIDER,
        )
        original_charge = int(document.array.charge[0])
        session_id = store.add(document)
        store.call(session_id, lambda item: item.set_charge([0], value=original_charge + 1), persist=True)
        assert store.call(session_id, lambda item: item.undo(), persist=True) is True
        assert store.call(session_id, lambda item: int(item.array.charge[0])) == original_charge
    finally:
        reset_current_owner(token)
    token = set_current_owner("bob")
    try:
        with pytest.raises(KeyError):
            store.get(session_id)
    finally:
        reset_current_owner(token)


def test_shared_sessions_enforce_per_owner_limit(repository: PostgresJobRepository) -> None:
    store = PostgresDocumentStore(
        repository,
        condition_provider=NEUTRAL_CONDITION_PROVIDER,
        validation_provider=NEUTRAL_VALIDATION_PROVIDER,
        max_sessions_per_user=1,
    )
    token = set_current_owner("alice")
    try:
        store.add(StudioDocument.from_cif(FIXTURE.read_text(), "one"))
        with pytest.raises(CapacityExceededError, match="session limit"):
            store.add(StudioDocument.from_cif(FIXTURE.read_text(), "two"))
    finally:
        reset_current_owner(token)


def test_concurrent_claims_enforce_per_user_running_quota(
    repository: PostgresJobRepository,
    tmp_path: Path,
) -> None:
    provider = _provider(repository, tmp_path)
    for index in range(2):
        provider.submit(
            FIXTURE.read_text(),
            f"input-{index}",
            DesignJobRequest(num_designs=1),
            owner_id="alice",
        )
    for worker_id in ("worker-a", "worker-b"):
        repository.register_worker(
            worker_id,
            revision="test-revision",
            capabilities=["rfd4"],
            gpu_name=None,
        )

    def claim(worker_id: str) -> dict | None:
        return repository.claim(
            worker_id,
            revision="test-revision",
            capabilities=["rfd4"],
            lease_seconds=60,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ("worker-a", "worker-b")))
    assert sum(claimed is not None for claimed in claims) == 1
    with repository.connection() as connection:
        quota = connection.execute("SELECT running_jobs FROM studio_user_quotas WHERE owner_id = 'alice'").fetchone()
    assert quota["running_jobs"] == 1
