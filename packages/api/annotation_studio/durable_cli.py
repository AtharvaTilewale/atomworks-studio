"""Operator commands for durable Studio migrations and GPU workers."""

from __future__ import annotations

import argparse
import os
import shlex
import signal
import socket
from pathlib import Path
from threading import Event

from .durable_jobs import DurableWorker, PostgresJobRepository, object_store_from_url


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atomworks-studio-durable")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply pending PostgreSQL schema migrations")
    quota = commands.add_parser("set-quota", help="set one authenticated user's GPU limits")
    quota.add_argument("owner_id")
    quota.add_argument("--max-queued", type=int, required=True)
    quota.add_argument("--max-running", type=int, required=True)
    quota.add_argument("--weight", type=float, default=1.0)
    commands.add_parser("cleanup", help="remove expired sessions, job records, and artifacts")
    worker = commands.add_parser("worker", help="run one leased RFD4 worker (one process per GPU)")
    worker.add_argument("--once", action="store_true", help="claim at most one job")
    return parser


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def main() -> None:
    arguments = _parser().parse_args()
    repository = PostgresJobRepository(_required("ANNOTATION_STUDIO_DATABASE_URL"))
    try:
        if arguments.command == "migrate":
            applied = repository.migrate()
            print("applied migrations: " + (", ".join(applied) if applied else "none"))
            return
        if arguments.command == "set-quota":
            repository.set_quota(
                arguments.owner_id,
                max_queued=arguments.max_queued,
                max_running=arguments.max_running,
                scheduling_weight=arguments.weight,
            )
            print(f"updated quota for {arguments.owner_id}")
            return
        store = object_store_from_url(_required("ANNOTATION_STUDIO_OBJECT_STORE_URL"))
        if arguments.command == "cleanup":
            keys = repository.delete_expired(
                retention_seconds=int(os.environ.get("ANNOTATION_STUDIO_JOB_RETENTION_SECONDS", str(7 * 86400)))
            )
            failures = 0
            for key in keys:
                try:
                    store.delete(key)
                except Exception as error:
                    failures += 1
                    repository.record_object_deletion_failure(key, str(error))
                    print(f"could not delete {key}: {error}")
                else:
                    repository.acknowledge_object_deletion(key)
            print(f"deleted {len(keys) - failures} artifacts; {failures} deletion failures")
            if failures:
                raise SystemExit(1)
            return
        worker = DurableWorker(
            repository,
            store,
            worker_id=os.environ.get("ANNOTATION_STUDIO_WORKER_ID", f"{socket.gethostname()}-{os.getpid()}"),
            revision=_required("ANNOTATION_STUDIO_WORKER_REVISION"),
            capabilities=os.environ.get("ANNOTATION_STUDIO_WORKER_CAPABILITIES", "rfd4").split(","),
            project_root=Path(_required("ANNOTATION_STUDIO_RFD4_ROOT")),
            command=shlex.split(_required("ANNOTATION_STUDIO_RFD4_COMMAND")),
            staging_root=Path(_required("ANNOTATION_STUDIO_WORKER_STAGING_DIR")),
            gpu_name=os.environ.get("ANNOTATION_STUDIO_GPU_NAME"),
            lease_seconds=int(os.environ.get("ANNOTATION_STUDIO_WORKER_LEASE_SECONDS", "90")),
            heartbeat_seconds=int(os.environ.get("ANNOTATION_STUDIO_WORKER_HEARTBEAT_SECONDS", "20")),
            timeout_seconds=int(os.environ.get("ANNOTATION_STUDIO_JOB_TIMEOUT_SECONDS", str(4 * 60 * 60))),
        )
        stopping = Event()

        def request_stop(_signal_number: int, _frame: object) -> None:
            stopping.set()
            worker.stop()

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        while not stopping.is_set():
            worked = worker.run_once()
            if arguments.once:
                return
            if not worked:
                stopping.wait(min(30, int(os.environ.get("ANNOTATION_STUDIO_WORKER_POLL_SECONDS", "5"))))
    finally:
        repository.close()


if __name__ == "__main__":
    main()
