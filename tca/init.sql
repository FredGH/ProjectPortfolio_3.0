-- ============================================================
-- PrivateBank TCA Platform — PostgreSQL initialisation
-- Creates all schemas + non-dlt/non-dbt tables.
-- dlt creates stg_raw tables; dbt creates vault/mart tables.
-- ============================================================

-- Airflow metadata lives in a separate database
CREATE DATABASE airflow_db;

-- Enable TimescaleDB (required before schema creation)
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- ============================================================
-- SCHEMAS
-- ============================================================
CREATE SCHEMA IF NOT EXISTS stg_raw;       -- dlt landing zone (immutable)
CREATE SCHEMA IF NOT EXISTS raw_vault;     -- Data Vault 2.0: Hubs, Links, Satellites
CREATE SCHEMA IF NOT EXISTS biz_vault;     -- Business Vault: derived, versioned
CREATE SCHEMA IF NOT EXISTS mart_trading_risk;
CREATE SCHEMA IF NOT EXISTS mart_market_data;
CREATE SCHEMA IF NOT EXISTS mart_corporate;
CREATE SCHEMA IF NOT EXISTS mart_consolidated;
CREATE SCHEMA IF NOT EXISTS obs;           -- Observability
CREATE SCHEMA IF NOT EXISTS catalog;       -- Metadata catalog
CREATE SCHEMA IF NOT EXISTS auth;          -- JWT refresh tokens + API clients

-- ============================================================
-- OBSERVABILITY TABLES  (not managed by dlt or dbt)
-- ============================================================
CREATE TABLE IF NOT EXISTS obs.quarantine_queue (
    id                BIGSERIAL     PRIMARY KEY,
    record_id         VARCHAR(40),
    source_table      VARCHAR(100),
    failed_check      VARCHAR(100),
    severity          VARCHAR(10)   NOT NULL CHECK (severity IN ('hard','soft')),
    original_payload  JSONB,
    quarantine_reason TEXT,
    quarantine_time   TIMESTAMPTZ   DEFAULT NOW(),
    resolution_status VARCHAR(20)   DEFAULT 'pending'
                          CHECK (resolution_status IN ('pending','corrected','rejected'))
);

CREATE TABLE IF NOT EXISTS obs.obs_warnings (
    id              BIGSERIAL    PRIMARY KEY,
    check_name      VARCHAR(100),
    affected_table  VARCHAR(100),
    affected_rows   INTEGER,
    warn_value      TEXT,
    warn_time       TIMESTAMPTZ  DEFAULT NOW()
);

-- ============================================================
-- METADATA CATALOG  (populated by post-dbt-run hook)
-- ============================================================
CREATE TABLE IF NOT EXISTS catalog.datasets (
    id              BIGSERIAL     PRIMARY KEY,
    table_name      VARCHAR(100)  NOT NULL,
    schema_name     VARCHAR(100)  NOT NULL,
    domain          VARCHAR(50),
    owner_team      VARCHAR(50),
    row_count       BIGINT,
    last_dbt_run    TIMESTAMPTZ,
    quality_status  VARCHAR(10)   DEFAULT 'GREEN'
                        CHECK (quality_status IN ('GREEN','AMBER','RED')),
    schema_hash     VARCHAR(64),  -- MD5 of column names+types; mismatch = WARN
    pii_sensitive   BOOLEAN       DEFAULT FALSE,
    created_at      TIMESTAMPTZ   DEFAULT NOW(),
    updated_at      TIMESTAMPTZ   DEFAULT NOW(),
    UNIQUE (table_name, schema_name)
);

-- ============================================================
-- AUTH TABLES  (JWT refresh tokens + API client registry)
-- ============================================================
CREATE TABLE IF NOT EXISTS auth.api_clients (
    client_id        VARCHAR(50)   PRIMARY KEY,
    client_secret_hash VARCHAR(128) NOT NULL,  -- bcrypt hash
    role             VARCHAR(20)   NOT NULL
                         CHECK (role IN ('TRADER','HEAD_OF_TRADING','COMPLIANCE','CLIENT','ADMIN')),
    counterparty_id  VARCHAR(50),              -- non-null for CLIENT role
    legal_entity     VARCHAR(20),
    is_active        BOOLEAN       DEFAULT TRUE,
    created_at       TIMESTAMPTZ   DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS auth.refresh_tokens (
    id              BIGSERIAL     PRIMARY KEY,
    client_id       VARCHAR(50)   NOT NULL REFERENCES auth.api_clients(client_id),
    token_hash      VARCHAR(128)  NOT NULL UNIQUE,  -- SHA-256 hash of token
    expires_at      TIMESTAMPTZ   NOT NULL,
    revoked         BOOLEAN       DEFAULT FALSE,
    created_at      TIMESTAMPTZ   DEFAULT NOW()
);

-- ============================================================
-- SEED: default API clients for PoC testing
-- Passwords in .env.example — hashes here are bcrypt of "changeme"
-- ============================================================
INSERT INTO auth.api_clients (client_id, client_secret_hash, role, counterparty_id, legal_entity)
VALUES
    ('trader_01',     '$2b$12$placeholder_hash_trader',    'TRADER',          NULL,       'PB_DE'),
    ('compliance_01', '$2b$12$placeholder_hash_comply',    'COMPLIANCE',      NULL,       'PB_DE'),
    ('head_trading',  '$2b$12$placeholder_hash_head',      'HEAD_OF_TRADING', NULL,       'PB_DE'),
    ('client_cp_a',   '$2b$12$placeholder_hash_client_a',  'CLIENT',         'CP_ABCD',  'PB_DE'),
    ('client_cp_b',   '$2b$12$placeholder_hash_client_b',  'CLIENT',         'CP_EFGH',  'PB_UK'),
    ('admin_01',      '$2b$12$placeholder_hash_admin',     'ADMIN',           NULL,       'PB_DE')
ON CONFLICT (client_id) DO NOTHING;

-- ============================================================
-- RT FILLS TABLE  (written directly by redis_consumer.py)
-- ============================================================
CREATE TABLE IF NOT EXISTS stg_raw.rt_fills (
    id                BIGSERIAL     PRIMARY KEY,
    stream_id         VARCHAR(40)   UNIQUE,       -- Redis stream entry ID
    fill_id           VARCHAR(40)   NOT NULL,
    order_id          VARCHAR(40)   NOT NULL,
    instrument_id     VARCHAR(20)   NOT NULL,
    instrument_class  VARCHAR(20)   NOT NULL,
    counterparty_id   VARCHAR(20)   NOT NULL,
    side              VARCHAR(4)    NOT NULL CHECK (side IN ('BUY','SELL')),
    fill_price        DOUBLE PRECISION NOT NULL,
    fill_quantity     BIGINT        NOT NULL,
    venue_id          VARCHAR(20),
    fill_time         TIMESTAMPTZ   NOT NULL,
    market_impact_bps DOUBLE PRECISION,
    commission_bps    DOUBLE PRECISION,
    currency          VARCHAR(3),
    received_at       TIMESTAMPTZ   DEFAULT NOW()
);

-- ============================================================
-- TimescaleDB hypertable pre-declaration
-- dlt creates stg_raw.tick_bars; we convert it after first dlt run.
-- The DO block is idempotent — runs only if the table exists.
-- ============================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'stg_raw' AND table_name = 'tick_bars'
    ) THEN
        PERFORM create_hypertable(
            'stg_raw.tick_bars', 'ts',
            if_not_exists => TRUE,
            chunk_time_interval => INTERVAL '7 days'
        );
    END IF;
END
$$;

-- ============================================================
-- GRANTS
-- ============================================================
GRANT ALL ON SCHEMA stg_raw            TO tca_user;
GRANT ALL ON SCHEMA raw_vault          TO tca_user;
GRANT ALL ON SCHEMA biz_vault          TO tca_user;
GRANT ALL ON SCHEMA mart_trading_risk  TO tca_user;
GRANT ALL ON SCHEMA mart_market_data   TO tca_user;
GRANT ALL ON SCHEMA mart_corporate     TO tca_user;
GRANT ALL ON SCHEMA mart_consolidated  TO tca_user;
GRANT ALL ON SCHEMA obs                TO tca_user;
GRANT ALL ON SCHEMA catalog            TO tca_user;
GRANT ALL ON SCHEMA auth               TO tca_user;

GRANT ALL ON ALL TABLES    IN SCHEMA obs     TO tca_user;
GRANT ALL ON ALL TABLES    IN SCHEMA catalog TO tca_user;
GRANT ALL ON ALL TABLES    IN SCHEMA auth    TO tca_user;
GRANT ALL ON ALL SEQUENCES IN SCHEMA obs     TO tca_user;
GRANT ALL ON ALL SEQUENCES IN SCHEMA catalog TO tca_user;
GRANT ALL ON ALL SEQUENCES IN SCHEMA auth    TO tca_user;
