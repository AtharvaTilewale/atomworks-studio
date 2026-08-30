CREATE TABLE IF NOT EXISTS studio_schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS studio_user_quotas (
    owner_id text PRIMARY KEY,
    max_queued integer NOT NULL DEFAULT 8 CHECK (max_queued > 0),
    max_running integer NOT NULL DEFAULT 1 CHECK (max_running > 0 AND max_running <= max_queued),
    running_jobs integer NOT NULL DEFAULT 0 CHECK (running_jobs >= 0),
    scheduling_weight double precision NOT NULL DEFAULT 1 CHECK (
        scheduling_weight > 0 AND scheduling_weight <= 1000
    ),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS studio_sessions (
    id text PRIMARY KEY,
    owner_id text NOT NULL,
    name text NOT NULL,
    cif text NOT NULL,
    undo_cifs jsonb NOT NULL DEFAULT '[]'::jsonb,
    revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS studio_sessions_owner_updated
    ON studio_sessions (owner_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS studio_sessions_expiry ON studio_sessions (expires_at);

CREATE TABLE IF NOT EXISTS studio_design_jobs (
    id uuid PRIMARY KEY,
    owner_id text NOT NULL,
    idempotency_key text,
    request_fingerprint text NOT NULL,
    provider text NOT NULL DEFAULT 'durable-rfd4',
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    priority smallint NOT NULL DEFAULT 0 CHECK (priority BETWEEN -10 AND 10),
    required_capabilities jsonb NOT NULL DEFAULT '[]'::jsonb,
    required_revision text,
    input_name text NOT NULL,
    source_session_id text,
    request jsonb NOT NULL,
    input_object_key text NOT NULL,
    outputs jsonb NOT NULL DEFAULT '[]'::jsonb,
    metric_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    log_object_key text,
    phase text NOT NULL DEFAULT 'Waiting for GPU',
    error text,
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts integer NOT NULL DEFAULT 2 CHECK (max_attempts BETWEEN 1 AND 10),
    lease_owner text,
    lease_token uuid,
    lease_expires_at timestamptz,
    heartbeat_at timestamptz,
    cancel_requested_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    attempt_started_at timestamptz,
    completed_at timestamptz,
    gpu_seconds double precision CHECK (
        gpu_seconds IS NULL OR (gpu_seconds >= 0 AND gpu_seconds < 'Infinity'::double precision)
    ),
    CONSTRAINT studio_design_jobs_lease_consistent CHECK (
        (
            status = 'running' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL
            AND lease_expires_at IS NOT NULL AND attempt_started_at IS NOT NULL
        ) OR (
            status <> 'running' AND lease_owner IS NULL AND lease_token IS NULL
            AND lease_expires_at IS NULL AND attempt_started_at IS NULL
        )
    ),
    CONSTRAINT studio_design_jobs_terminal_time CHECK (
        (status IN ('completed', 'failed', 'cancelled')) = (completed_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS studio_design_jobs_owner_idempotency
    ON studio_design_jobs (owner_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS studio_design_jobs_dispatch
    ON studio_design_jobs (status, priority DESC, created_at)
    WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS studio_design_jobs_owner_created
    ON studio_design_jobs (owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS studio_design_jobs_stale_lease
    ON studio_design_jobs (lease_expires_at)
    WHERE status = 'running';

CREATE TABLE IF NOT EXISTS studio_workers (
    id text PRIMARY KEY,
    revision text NOT NULL,
    capabilities jsonb NOT NULL DEFAULT '[]'::jsonb,
    gpu_name text,
    gpu_count integer NOT NULL DEFAULT 1 CHECK (gpu_count > 0),
    state text NOT NULL DEFAULT 'idle' CHECK (state IN ('idle', 'busy', 'draining')),
    current_job_id uuid REFERENCES studio_design_jobs(id) ON DELETE SET NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS studio_workers_heartbeat ON studio_workers (heartbeat_at);

CREATE TABLE IF NOT EXISTS studio_job_events (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id uuid NOT NULL,
    owner_id text NOT NULL,
    event_type text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS studio_job_events_job_sequence
    ON studio_job_events (job_id, sequence);
CREATE INDEX IF NOT EXISTS studio_job_events_owner_created
    ON studio_job_events (owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS studio_usage_ledger (
    job_id uuid PRIMARY KEY,
    owner_id text NOT NULL,
    gpu_seconds double precision NOT NULL DEFAULT 0 CHECK (
        gpu_seconds >= 0 AND gpu_seconds < 'Infinity'::double precision
    ),
    final_status text NOT NULL CHECK (final_status IN ('completed', 'failed', 'cancelled')),
    completed_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS studio_usage_ledger_owner_completed
    ON studio_usage_ledger (owner_id, completed_at DESC);

CREATE TABLE IF NOT EXISTS studio_pending_object_deletions (
    object_key text PRIMARY KEY,
    attempts integer NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION studio_record_job_event() RETURNS trigger AS $$
DECLARE
    event_name text;
    event_worker text;
BEGIN
    IF TG_OP = 'UPDATE'
       AND NEW.status IN ('completed', 'failed', 'cancelled')
       AND NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO studio_usage_ledger (job_id, owner_id, gpu_seconds, final_status, completed_at)
        VALUES (NEW.id, NEW.owner_id, COALESCE(NEW.gpu_seconds, 0), NEW.status, COALESCE(NEW.completed_at, now()))
        ON CONFLICT (job_id) DO UPDATE SET
            gpu_seconds = EXCLUDED.gpu_seconds,
            final_status = EXCLUDED.final_status,
            completed_at = EXCLUDED.completed_at;
    END IF;
    IF TG_OP = 'INSERT' THEN
        event_name := 'submitted';
        event_worker := NEW.lease_owner;
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        event_name := NEW.status;
        event_worker := COALESCE(NEW.lease_owner, OLD.lease_owner);
    ELSIF NEW.cancel_requested_at IS NOT NULL AND OLD.cancel_requested_at IS NULL THEN
        event_name := 'cancellation_requested';
        event_worker := COALESCE(NEW.lease_owner, OLD.lease_owner);
    ELSE
        RETURN NEW;
    END IF;
    INSERT INTO studio_job_events (job_id, owner_id, event_type, details)
    VALUES (
        NEW.id,
        NEW.owner_id,
        event_name,
        jsonb_build_object('attempt', NEW.attempt, 'worker_id', event_worker)
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS studio_design_jobs_audit ON studio_design_jobs;
CREATE TRIGGER studio_design_jobs_audit
AFTER INSERT OR UPDATE ON studio_design_jobs
FOR EACH ROW EXECUTE FUNCTION studio_record_job_event();
