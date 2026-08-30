# Durable multi-user deployment

This topology separates the CPU control plane from replaceable GPU workers. It
is the supported path for many authenticated users; `ProcessDesignProvider`
remains the deliberately serialized local/Brev pilot.

## Boundaries

- An OIDC-aware reverse proxy authenticates the browser, removes any incoming
  identity header, and injects the immutable provider subject in the header
  configured by `ANNOTATION_STUDIO_IDENTITY_HEADER`. Do not use a mutable
  display name (or email unless the provider guarantees it is immutable).
  Restrict the API Service so clients cannot bypass that proxy and forge the
  header.
- Two or more Studio API replicas share PostgreSQL session and job state. Each
  mutation locks one session row, so undo history and revisions survive replica
  changes without sticky sessions.
- Inputs, outputs, and logs use an S3-compatible bucket. PostgreSQL stores only
  object keys and bounded metadata.
- Run exactly one `atomworks-studio-durable worker` process per visible GPU.
  Workers atomically reserve a per-user slot, claim with `FOR UPDATE SKIP
  LOCKED`, heartbeat a lease, and publish only if they still own that lease.
- A worker advertises its immutable RFD4/Studio revision and capabilities such
  as `rfd4,af3`; incompatible jobs remain queued. Expired leases are recovered
  and retried up to the configured bound.

Workers need private-network PostgreSQL access and a bucket policy limited to
the Studio job prefix. For Brev instances outside that private network, attach
them with the organization VPN/private link; do not expose PostgreSQL publicly.
Long-lived cloud or Brev provisioning credentials do not belong in either the
API or worker. An autoscaler may create instances separately from queue depth.

## Control-plane configuration

Install `atomworks-annotation-studio[durable]`, then set:

```text
ANNOTATION_STUDIO_DATABASE_URL=postgresql://...
ANNOTATION_STUDIO_OBJECT_STORE_URL=s3://studio-production/jobs
ANNOTATION_STUDIO_S3_ENDPOINT_URL=https://...       # only for non-AWS S3
ANNOTATION_STUDIO_IDENTITY_HEADER=X-Auth-Request-User
ANNOTATION_STUDIO_TRUST_PROXY_IDENTITY=1
ANNOTATION_STUDIO_REQUIRED_WORKER_REVISION=<git-sha>
ANNOTATION_STUDIO_AVAILABLE_CAPABILITIES=rfd4,af3
ANNOTATION_STUDIO_METRICS_TOKEN=<secret>
```

Apply migrations once before an API rollout:

```bash
atomworks-studio-durable migrate
```

Defaults apply on first submission. Override a user without restarting APIs:

```bash
atomworks-studio-durable set-quota user@example.org --max-queued 12 --max-running 2 --weight 1
```

Run `atomworks-studio-durable cleanup` from one scheduled job. It removes
expired sessions and terminal jobs older than
`ANNOTATION_STUDIO_JOB_RETENTION_SECONDS` (seven days by default), then drains
the transactional object-deletion outbox. Failed S3 deletions remain queued for
the next run instead of losing their audit trail. The compact per-user GPU-time
ledger is retained after bulky job metadata and artifacts are removed; apply
the organization's billing/privacy retention policy to that table separately.

`/api/ready` verifies the durable store. `/api/v1/metrics` returns
low-cardinality queue, worker, failure, and GPU-second metrics only when called
with `Authorization: Bearer $ANNOTATION_STUDIO_METRICS_TOKEN`. User ownership
and accumulated GPU seconds remain available in PostgreSQL for private cost
attribution without putting user identifiers into Prometheus labels.

The example [`kubernetes.yaml`](kubernetes.yaml) uses multiple stateless API
replicas and external managed PostgreSQL/S3 secrets. Replace every `${...}`
placeholder in the release pipeline; it intentionally does not deploy a
database or object store inside the application namespace.

## GPU worker configuration

On each reviewed GPU image/Launchable, provide the same database and object
store settings plus:

```text
ANNOTATION_STUDIO_WORKER_ID=<unique-instance-and-gpu-id>
ANNOTATION_STUDIO_WORKER_REVISION=<git-sha>
ANNOTATION_STUDIO_WORKER_CAPABILITIES=rfd4,af3
ANNOTATION_STUDIO_RFD4_ROOT=/opt/RFD4-Proteina-dev
ANNOTATION_STUDIO_RFD4_COMMAND=/opt/rfd4-venv/bin/python /opt/RFD4-Proteina-dev/src/rfproteina/generate.py
ANNOTATION_STUDIO_WORKER_STAGING_DIR=/var/tmp/studio-worker
```

The model subprocess receives only runtime paths, CUDA/AF3 settings, and other
non-secret essentials. If a model needs another non-secret variable, list its
name in `ANNOTATION_STUDIO_WORKER_CHILD_ENV_ALLOWLIST`. Studio, cloud-provider,
and object-store credential names cannot be added through that setting.

Select one GPU before starting each process, for example:

```bash
CUDA_VISIBLE_DEVICES=0 atomworks-studio-durable worker
```

The command does not daemonize. Use systemd, Kubernetes, or the Brev supervisor
for restart policy. Drain an instance by stopping its worker; a job whose lease
expires is safely recovered. Scale from queue age and capability-specific queue
depth, keep a small warm minimum for compile latency, and cap the pool using the
remaining Brev-credit budget.

## Rollout and failure tests

Before admitting users:

1. Submit the same request twice with one `Idempotency-Key`; confirm one job.
2. Submit two jobs for one user and race two workers; confirm the configured
   running quota is never exceeded.
3. Kill a worker after claim, wait past its lease, and confirm retry/recovery.
4. Cancel a running job and confirm the worker terminates its owned subprocess.
5. Roll API replicas during an edit and confirm session revision and undo state.
6. Test one RFD4-only and one AF3-tagged job against capability-matched workers.
7. Alert on oldest queue age, no healthy compatible workers, failures, database
   readiness, object-store errors, GPU seconds, and credit/budget exhaustion.
