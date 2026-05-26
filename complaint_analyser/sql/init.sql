CREATE TABLE IF NOT EXISTS triage_results (
    input_id              TEXT PRIMARY KEY,
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
    processed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_created_at     TIMESTAMPTZ
);

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
