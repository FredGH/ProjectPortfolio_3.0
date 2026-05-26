CREATE TABLE IF NOT EXISTS triage_results (
    input_id              TEXT PRIMARY KEY,
    batch_id              TEXT NOT NULL DEFAULT '',
    domain                TEXT NOT NULL,
    priority              TEXT NOT NULL,
    composite_score       FLOAT NOT NULL,
    dimension_scores      JSONB NOT NULL,
    confidence            FLOAT NOT NULL,
    low_confidence_reason TEXT,
    triggered_keywords    TEXT[] NOT NULL DEFAULT '{}',
    retrieved_references  JSONB NOT NULL DEFAULT '{}',
    reasoning             TEXT NOT NULL,
    recommended_action    TEXT NOT NULL,
    analyst_override      TEXT,
    is_auto_p4            BOOLEAN NOT NULL DEFAULT FALSE,
    processed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_created_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_triage_results_batch_id
    ON triage_results (batch_id);

CREATE INDEX IF NOT EXISTS idx_triage_results_domain_priority
    ON triage_results (domain, priority, processed_at DESC);

CREATE INDEX IF NOT EXISTS idx_triage_results_analyst_override
    ON triage_results (analyst_override)
    WHERE analyst_override IS NOT NULL;

CREATE TABLE IF NOT EXISTS triage_batches (
    batch_id     TEXT PRIMARY KEY,
    domain       TEXT NOT NULL,
    total        INT NOT NULL,
    done         INT NOT NULL DEFAULT 0,
    failed       INT NOT NULL DEFAULT 0,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS recalibration_alerts (
    id            SERIAL PRIMARY KEY,
    priority      TEXT NOT NULL,
    override_rate FLOAT NOT NULL,
    window_start  TIMESTAMPTZ NOT NULL,
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drift_events (
    id            SERIAL PRIMARY KEY,
    cluster_id    TEXT NOT NULL,
    label         TEXT NOT NULL,
    size          INT NOT NULL,
    pct_of_volume FLOAT NOT NULL,
    sample_ids    TEXT[] NOT NULL DEFAULT '{}',
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at   TIMESTAMPTZ
);
