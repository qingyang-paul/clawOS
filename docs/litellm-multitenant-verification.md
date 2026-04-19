# LiteLLM Multi-tenant Virtual Key Verification

## Goal

Verify:

1. different tenants hold different LiteLLM virtual keys
2. requests from tenant keys are forwarded through one LiteLLM relay
3. records can be queried separately (`/key/info`, `/spend/logs`)

## Commands

Set a master key in current shell:

```bash
export LITELLM_MASTER_KEY="sk-verify-strong-master-key"
```

Requirements:

- `LITELLM_MASTER_KEY` must start with `sk-`
- virtual key management needs database; verification compose starts an internal postgres automatically

Start verification stack:

```bash
make litellm-verify-up
make litellm-verify-status
```

Run multi-tenant virtual-key verification:

```bash
make litellm-verify-run \
  LITELLM_VERIFY_TENANT_A=tenant-0001 \
  LITELLM_VERIFY_TENANT_B=tenant-0002
```

Expected output includes:

- two different generated keys
- successful requests for tenant A and tenant B
- key-specific records from `/key/info`
- spend log preview from `/spend/logs` (when endpoint is healthy)

Note:

- LiteLLM `main-v1.35.0` may return 500 on `/spend/logs` for some setups.
- In that case, verification still passes using key-level isolation (`/key/info`) and successful per-key requests.

Inspect runtime logs:

```bash
make litellm-verify-logs
```

Stop stack:

```bash
make litellm-verify-down
```

## Files

- `core/litellm/compose.verification.yaml`
- `core/litellm/config.yaml`
- `core/litellm/mock_openai_server.py`
- `core/litellm/verify_virtual_keys.py`
