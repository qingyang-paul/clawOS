BEGIN;

CREATE TABLE IF NOT EXISTS tenant (
    id VARCHAR(64) PRIMARY KEY,
    name TEXT NOT NULL,
    channel_type TEXT NOT NULL,
    status TEXT NOT NULL,
    route_prefix TEXT NOT NULL,
    image_tag TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS wallet (
    tenant_id VARCHAR(64) PRIMARY KEY REFERENCES tenant(id) ON DELETE CASCADE,
    balance NUMERIC(18, 6) NOT NULL,
    currency CHAR(3) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    delta NUMERIC(18, 6) NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_tenant_id_created_at
    ON ledger (tenant_id, created_at);

CREATE TABLE IF NOT EXISTS tenant_provision_job (
    id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tenant_provision_job_tenant_id_created_at
    ON tenant_provision_job (tenant_id, created_at);

COMMIT;
