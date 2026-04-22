# Deployment Baseline (v1)

## Goal

Expose only the gateway to public internet and keep all internal services private by default.

## Network Topology

- `clawos_public`: ingress only, Traefik attached, host ports `80/443`.
- `clawos_internal`: service communication, Traefik + tenant services + LiteLLM.
- `clawos_secure`: data plane, LiteLLM + PostgreSQL, internal-only network.

Security objective:
- Tenants do not join `clawos_secure`.
- PostgreSQL is never exposed with host `ports`.

## Inbound/Outbound Policy

- Inbound allowed only on cloud firewall/security group:
  - `22` (restricted source IPs)
  - `80`
  - `443`
- Default outbound is allowed for required upstream calls:
  - OpenAI API access by LiteLLM/OpenClaw
  - ACME challenge traffic for TLS certificate issuance

## Traefik Baseline

- TLS enabled with Let's Encrypt (HTTP-01).
- Dashboard must not be public without hardening.
- If dashboard is enabled, require both:
  - BasicAuth
  - source IP allowlist or private management subdomain
- Undefined routes must return `404`.

## Image Policy

- Do not use `latest`.
- Pin explicit tags in compose files.
- Validate image existence before first rollout.

Recommended initial tags:
- `openclaw/lobster:1.4.2-alpine`
- `ghcr.io/berriai/litellm:v1.83.3-stable`

## Backup/Restore Baseline

- Backup command: `clawos backup`.
- Output path: `clawos/.tmp/backups/`.
- Artifact contents:
  - PostgreSQL dump (`pg_dump`)
  - tenant data archives (`tar`)
- Restore command: `clawos restore --file <backup_path>`.

## Preflight Checklist

- Docker daemon running.
- Required networks exist.
- Required config files exist (`core/compose.yaml`, tenant `compose.yaml`, `clawos.toml`).
- Required environment keys pass validation.
