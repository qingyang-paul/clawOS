# User Ledger And User Balance Tables

## Goal

Provide database tables for:

- per-user balance in a tenant
- append-only user ledger for billing/audit

## Tables

`user_balance`

- primary key: `(tenant_id, user_id)`
- fields: `balance`, `currency`, `updated_at`
- purpose: current balance snapshot for each user in tenant scope

`user_ledger`

- append-only user balance change records
- idempotency key: `UNIQUE (tenant_id, user_id, idempotency_key)`
- key dimensions: `tenant_id`, `user_id`, `event_type`, `source`, `source_ref`
- amount fields: `delta`, `balance_after`, `currency`
- audit fields: `request_id`, `metadata`, `occurred_at`, `created_at`
- `event_type`: `llm_usage`, `topup`, `refund`, `adjustment`, `reversal`

## Indexes

- `idx_user_balance_tenant_updated_at`
- `idx_user_ledger_tenant_created_at`
- `idx_user_ledger_tenant_user_occurred_at`
- `idx_user_ledger_source_ref`
