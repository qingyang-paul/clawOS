# Tenant Control Plane (v1)

## Goal

Provide a company-internal API to create tenant runtime instances.  
Frontend only sends user/business fields; backend generates and owns runtime fields.

## Request/Response Contract

Endpoint:

- `POST /v1/tenants`

Frontend required input:

- `tenant_name`
- `channel_type` (currently supports `feishu`)
- `channel_config` (for `feishu`: `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_VERIFICATION_TOKEN`)

Frontend forbidden input:

- `tenant_id`
- `route_prefix`
- `image_tag`
- `OPENAI_API_BASE`
- `OPENAI_API_KEY`
- `LOG_LEVEL`

Backend generated fields:

- `tenant_id`
- `route_prefix`
- `image_tag`
- `OPENAI_API_BASE`
- `OPENAI_API_KEY` (currently via skeleton key provider; replace with LiteLLM virtual key provider later)
- `LOG_LEVEL`

## Runtime Flow

1. Validate request and channel config.
2. Allocate new `tenant_id`.
3. Generate `route_prefix=/tenant/{tenant_id}`.
4. Generate tenant OpenAI key (per-tenant unique key contract).
5. Persist tenant + provisioning job metadata.
6. Write tenant runtime files: `tenants/<tenant_id>/.env` and `tenants/<tenant_id>/compose.yaml`.
7. Run `docker compose up -d` for tenant.
8. Wait for Traefik router `<tenant_id>@docker` to appear.
9. Update tenant/job status to `RUNNING` / `DONE`.

## Data Model

`core/postgres/init.sql` now includes:

- `tenant` fields: `channel_type`, `route_prefix`, `image_tag`, `reason_code`, `updated_at`
- `tenant_provision_job` table for operation tracking

Current v1 implementation uses a JSON registry gateway for fast iteration:

- `.tmp/control-plane/tenant-registry.json`

PostgreSQL persistence is the next replacement step for the same gateway interface.

## Start Service

One-line start with Makefile:

```bash
cd /path/to/clawOS
cp docs/tenant-control-plane.env.example .tmp/control-plane.env
# edit .tmp/control-plane.env and fill every key explicitly
make control-plane-up
```

Check service state/logs:

```bash
make control-plane-status
make control-plane-logs
```

Stop service:

```bash
make control-plane-down
```

## Traefik Lifecycle (for cloud reboot recovery)

After cloud server reboot, bring Traefik stack back first:

```bash
make traefik-up
```

Useful commands:

```bash
make traefik-status
make traefik-routes
make traefik-logs
make traefik-restart
make traefik-down
```

If you need raw CLI, run with explicit arguments (no defaults for required config):

```bash
cd cli
uv run clawos control-plane serve \
  --host 0.0.0.0 \
  --port 18090 \
  --project-root /path/to/clawOS \
  --registry-file /path/to/clawOS/.tmp/control-plane/tenant-registry.json \
  --traefik-dashboard-api-url http://127.0.0.1:8080 \
  --traefik-max-wait-seconds 30 \
  --traefik-poll-interval-seconds 1 \
  --platform-image-tag openclaw/lobster:1.4.2-alpine \
  --platform-service-port 3000 \
  --platform-openai-api-base https://api.openai.com/v1 \
  --platform-openai-api-key <platform-key-placeholder> \
  --platform-log-level INFO \
  --tenant-key-prefix vk_tenant_ \
  --tenant-key-entropy-bytes 24
```

Health check:

```bash
curl -s http://127.0.0.1:18090/healthz
```

## Verify Tenant Creation End-to-End

Goal:

1. a new tenant container is running
2. Traefik has router for this tenant
3. you can interact with tenant service through Traefik

Create tenant:

```bash
make traefik-up
make control-plane-up
make tenant-create-feishu \
  TENANT_NAME="acme-demo" \
  FEISHU_APP_ID="demo-app-id" \
  FEISHU_APP_SECRET="demo-app-secret" \
  FEISHU_VERIFICATION_TOKEN="demo-verify-token"
```

Take `tenant_id` from response (for example `tenant-0001`), then verify:

```bash
make tenant-verify TENANT_ID=tenant-0001
```

For quick interaction verification, keep:

- `PLATFORM_IMAGE_TAG=traefik/whoami:v1.10.2`
- `PLATFORM_SERVICE_PORT=80`

in `.tmp/control-plane.env`.  
Then `tenant-verify` returns whoami response body via `http://127.0.0.1/tenant/<tenant_id>`.
