# Traefik Multi-tenant Routing Demo

## Purpose

Run Traefik on a cloud VM and verify how requests from your local machine are routed to different tenants.

This demo does not include OpenClaw yet. It uses two mock tenant backends:
- `tenant_alpha` (`/tenant/alpha`)
- `tenant_beta` (`/tenant/beta`)

## Files

- `core/traefik/compose.multitenant-demo.yaml`
- `core/traefik/traefik.yaml`
- `core/traefik/ws_echo_server.py`
- `core/traefik/ws_echo_client.py`

## Start

```bash
cd core/traefik
mkdir -p logs
docker compose -f compose.multitenant-demo.yaml up -d
```

This demo uses Traefik docker provider + container labels for tenant routes.
Traefik is pinned to `v3.6.7`, and `DOCKER_API_VERSION=1.40` is set explicitly.

## Verify routing from local machine

Replace `<server_ip>` with your cloud server IP:

```bash
curl -s http://<server_ip>/tenant/alpha | head -n 20
curl -s http://<server_ip>/tenant/beta | head -n 20
```

Expected behavior:
- `/tenant/alpha` reaches the `tenant_alpha` backend.
- `/tenant/beta` reaches the `tenant_beta` backend.

## WebSocket long-connection test

The compose demo includes a WebSocket echo backend:
- Route: `/ws/echo`
- Backend container: `clawos-ws-echo`
- Backend port: `9000`

Start or refresh services:

```bash
cd core/traefik
docker compose -f compose.multitenant-demo.yaml up -d
```

Verify router is loaded:

```bash
curl -s http://localhost:18080/api/http/routers | jq '.[] | select(.name | contains("ws-echo")) | {name, rule, status}'
```

Run long-connection smoke test from your local machine:

```bash
python3 core/traefik/ws_echo_client.py \
  --url ws://<server_ip>/ws/echo \
  --count 30 \
  --interval 2 \
  --timeout 10
```

Expected behavior:
- Handshake returns `101 Switching Protocols`.
- All rounds print `echo_ok`.
- No unexpected disconnect before close.

Observe routing/access logs on cloud host:

```bash
cd core/traefik
tail -f logs/access.log | jq -r '"\(.RequestPath) \(.RouterName) \(.ServiceName) \(.DownstreamStatus)"'
```

For WebSocket requests, you should see entries like:
- `RequestPath=/ws/echo`
- `RouterName=ws-echo@docker`
- `ServiceName=ws-echo-svc@docker`

## Dashboard and monitoring

Traefik has a dashboard UI.

In this demo, dashboard and metrics are bound to loopback on the cloud host:
- Dashboard: `127.0.0.1:8080`
- Prometheus metrics: `127.0.0.1:8082`

Use SSH tunnel from your local machine:

```bash
ssh -L 18080:127.0.0.1:8080 -L 18082:127.0.0.1:8082 <user>@<server_ip>
```

Then open:
- `http://localhost:18080/dashboard/`
- `http://localhost:18082/metrics`

You can also inspect loaded routers from dashboard API:

```bash
curl -s http://localhost:18080/api/http/routers | jq '.[] | {name, rule, status}'
```

If routers are missing, verify docker provider status:

```bash
docker logs clawos-traefik --since 5m | grep -Ei "provider|docker|error"
```

## Request-level routing trace

Traefik access logs are written to `core/traefik/logs/access.log` (JSON format).

On the cloud host:

```bash
cd core/traefik
tail -f logs/access.log
```

Each log record contains fields such as `RouterName`, `ServiceName`, `RequestPath`, and `DownstreamStatus`.
These fields let you see exactly which router/service handled each tenant request.
