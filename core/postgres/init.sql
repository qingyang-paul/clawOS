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

CREATE TABLE IF NOT EXISTS tenant_channel_feishu (
    tenant_id VARCHAR(64) PRIMARY KEY REFERENCES tenant(id) ON DELETE CASCADE,
    app_id TEXT NOT NULL,
    app_secret TEXT NOT NULL,
    verification_token TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS tenant_virtual_key (
    tenant_id VARCHAR(64) PRIMARY KEY REFERENCES tenant(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    key_alias TEXT NOT NULL,
    virtual_key TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tenant_virtual_key_provider_status_updated_at
    ON tenant_virtual_key (provider, status, updated_at);

CREATE TABLE IF NOT EXISTS user_balance (
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL,
    balance NUMERIC(18, 6) NOT NULL,
    currency CHAR(3) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_user_balance_tenant_updated_at
    ON user_balance (tenant_id, updated_at);

CREATE TABLE IF NOT EXISTS user_ledger (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('llm_usage', 'topup', 'refund', 'adjustment', 'reversal')),
    delta NUMERIC(18, 6) NOT NULL,
    balance_after NUMERIC(18, 6) NOT NULL,
    currency CHAR(3) NOT NULL,
    source TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    request_id TEXT,
    idempotency_key TEXT NOT NULL,
    metadata JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (tenant_id, user_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_user_ledger_tenant_created_at
    ON user_ledger (tenant_id, created_at);

CREATE INDEX IF NOT EXISTS idx_user_ledger_tenant_user_occurred_at
    ON user_ledger (tenant_id, user_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_user_ledger_source_ref
    ON user_ledger (source, source_ref);

COMMIT;
