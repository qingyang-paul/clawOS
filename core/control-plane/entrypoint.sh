#!/usr/bin/env bash
set -euo pipefail

require_env() {
  local key="$1"
  local value=""
  eval "value=\${$key:-}"
  if [[ -z "$value" ]]; then
    echo "missing required env key: $key" >&2
    exit 1
  fi
}

normalize_traefik_api_url() {
  local input="$1"
  if [[ "$input" == "http://127.0.0.1:8080"* || "$input" == "http://localhost:8080"* ]]; then
    echo "http://clawos-traefik:8080"
    return
  fi
  echo "$input"
}

normalize_litellm_base_url() {
  local input="$1"
  if [[ "$input" == "http://127.0.0.1:4000"* || "$input" == "http://localhost:4000"* ]]; then
    echo "http://clawos-litellm-verify:4000"
    return
  fi
  echo "$input"
}

normalize_workspace_path() {
  local input="$1"
  if [[ "$input" == /workspace/* ]]; then
    echo "$input"
    return
  fi
  if [[ -n "${PROJECT_ROOT:-}" && "$input" == "$PROJECT_ROOT"* ]]; then
    echo "/workspace${input#"$PROJECT_ROOT"}"
    return
  fi
  if [[ "$input" == /root/clawOS/* ]]; then
    echo "/workspace${input#/root/clawOS}"
    return
  fi
  echo "$input"
}

require_env CONTROL_PLANE_PORT
require_env TRAEFIK_DASHBOARD_API_URL
require_env TRAEFIK_MAX_WAIT_SECONDS
require_env TRAEFIK_POLL_INTERVAL_SECONDS
require_env PLATFORM_IMAGE_TAG
require_env PLATFORM_SERVICE_PORT
require_env PLATFORM_OPENAI_API_BASE
require_env PLATFORM_OPENAI_API_KEY
require_env PLATFORM_LOG_LEVEL
require_env TENANT_KEY_PROVIDER
require_env LITELLM_AUTO_CHARGE_ENABLED

TRAEFIK_DASHBOARD_API_URL_EFFECTIVE="$(normalize_traefik_api_url "$TRAEFIK_DASHBOARD_API_URL")"
LITELLM_BASE_URL_EFFECTIVE="${LITELLM_BASE_URL:-}"
if [[ -n "$LITELLM_BASE_URL_EFFECTIVE" ]]; then
  LITELLM_BASE_URL_EFFECTIVE="$(normalize_litellm_base_url "$LITELLM_BASE_URL_EFFECTIVE")"
fi

args=(
  control-plane serve
  --host 0.0.0.0
  --port "$CONTROL_PLANE_PORT"
  --project-root /workspace
  --registry-file /workspace/.tmp/control-plane/tenant-registry.json
  --wallet-file /workspace/.tmp/control-plane/user-wallet.json
  --traefik-dashboard-api-url "$TRAEFIK_DASHBOARD_API_URL_EFFECTIVE"
  --traefik-max-wait-seconds "$TRAEFIK_MAX_WAIT_SECONDS"
  --traefik-poll-interval-seconds "$TRAEFIK_POLL_INTERVAL_SECONDS"
  --platform-image-tag "$PLATFORM_IMAGE_TAG"
  --platform-service-port "$PLATFORM_SERVICE_PORT"
  --platform-openai-api-base "$PLATFORM_OPENAI_API_BASE"
  --platform-openai-api-key "$PLATFORM_OPENAI_API_KEY"
  --platform-log-level "$PLATFORM_LOG_LEVEL"
  --tenant-key-provider "$TENANT_KEY_PROVIDER"
  --litellm-auto-charge-enabled "$LITELLM_AUTO_CHARGE_ENABLED"
)

if [[ -n "${TENANT_KEY_PREFIX:-}" ]]; then
  args+=(--tenant-key-prefix "$TENANT_KEY_PREFIX")
fi
if [[ -n "${TENANT_KEY_ENTROPY_BYTES:-}" ]]; then
  args+=(--tenant-key-entropy-bytes "$TENANT_KEY_ENTROPY_BYTES")
fi
if [[ -n "$LITELLM_BASE_URL_EFFECTIVE" ]]; then
  args+=(--litellm-base-url "$LITELLM_BASE_URL_EFFECTIVE")
fi
if [[ -n "${LITELLM_MASTER_KEY:-}" ]]; then
  args+=(--litellm-master-key "$LITELLM_MASTER_KEY")
fi
if [[ -n "${LITELLM_MODEL_NAME:-}" ]]; then
  args+=(--litellm-model-name "$LITELLM_MODEL_NAME")
fi
if [[ -n "${LITELLM_TIMEOUT_SECONDS:-}" ]]; then
  args+=(--litellm-timeout-seconds "$LITELLM_TIMEOUT_SECONDS")
fi
if [[ -n "${LITELLM_TOPUP_FX_RATES:-}" ]]; then
  args+=(--litellm-topup-fx-rates "$LITELLM_TOPUP_FX_RATES")
fi
if [[ -n "${LITELLM_TOPUP_SYNC_STATE_FILE:-}" ]]; then
  args+=(--litellm-topup-sync-state-file "$(normalize_workspace_path "$LITELLM_TOPUP_SYNC_STATE_FILE")")
fi
if [[ -n "${LITELLM_AUTO_CHARGE_POLL_INTERVAL_SECONDS:-}" ]]; then
  args+=(--litellm-auto-charge-poll-interval-seconds "$LITELLM_AUTO_CHARGE_POLL_INTERVAL_SECONDS")
fi
if [[ -n "${LITELLM_AUTO_CHARGE_STATE_FILE:-}" ]]; then
  args+=(--litellm-auto-charge-state-file "$(normalize_workspace_path "$LITELLM_AUTO_CHARGE_STATE_FILE")")
fi

exec python3 -m clawos_cli.interfaces.cli.main "${args[@]}"
