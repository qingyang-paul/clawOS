SHELL := /bin/bash

CONTROL_PLANE_ENV_FILE ?= .tmp/control-plane.env
CONTROL_PLANE_PID_FILE := .tmp/control-plane/control-plane.pid
CONTROL_PLANE_LOG_FILE := .tmp/control-plane/control-plane.log
CONTROL_PLANE_LAST_CREATE_FILE := .tmp/control-plane/last-create.json
CONTROL_PLANE_LAST_DELETE_FILE := .tmp/control-plane/last-delete.json
CONTROL_PLANE_API_BASE_URL ?= http://127.0.0.1:8080/control-plane
TRAEFIK_COMPOSE_FILE ?= core/traefik/compose.multitenant-demo.yaml
TRAEFIK_LOG_DIR ?= core/traefik/logs
TRAEFIK_DASHBOARD_API_URL ?= http://127.0.0.1:8080
LITELLM_VERIFY_COMPOSE_FILE ?= core/litellm/compose.verification.yaml
LITELLM_VERIFY_BASE_URL ?= http://127.0.0.1:4000
LITELLM_VERIFY_TENANT_A ?= tenant-verify-a
LITELLM_VERIFY_TENANT_B ?= tenant-verify-b

.PHONY: traefik-up traefik-down traefik-restart traefik-status traefik-logs traefik-routes litellm-verify-up litellm-verify-down litellm-verify-status litellm-verify-logs litellm-verify-run control-plane-up control-plane-down control-plane-status control-plane-logs tenant-create-feishu tenant-delete tenant-verify wallet-topup wallet-charge wallet-balance

traefik-up:
	@mkdir -p "$(TRAEFIK_LOG_DIR)"
	@docker compose -f "$(TRAEFIK_COMPOSE_FILE)" up -d
	@echo "traefik stack is up: $(TRAEFIK_COMPOSE_FILE)"

traefik-down:
	@docker compose -f "$(TRAEFIK_COMPOSE_FILE)" down
	@echo "traefik stack is down: $(TRAEFIK_COMPOSE_FILE)"

traefik-restart:
	@mkdir -p "$(TRAEFIK_LOG_DIR)"
	@docker compose -f "$(TRAEFIK_COMPOSE_FILE)" up -d --force-recreate
	@echo "traefik stack restarted: $(TRAEFIK_COMPOSE_FILE)"

traefik-status:
	@docker compose -f "$(TRAEFIK_COMPOSE_FILE)" ps

traefik-logs:
	@docker compose -f "$(TRAEFIK_COMPOSE_FILE)" logs -f --tail=200

traefik-routes:
	@curl -sS "$(TRAEFIK_DASHBOARD_API_URL)/api/http/routers" | python3 -c 'import json,sys; routers=json.load(sys.stdin); print("name\tstatus\tentryPoints\tservice\trule"); [print("{}\t{}\t{}\t{}\t{}".format(r.get("name", ""), r.get("status", ""), ",".join(r.get("entryPoints", []) or []), r.get("service", ""), r.get("rule", ""))) for r in routers]'

litellm-verify-up:
	@test -n "$(LITELLM_MASTER_KEY)" || (echo "LITELLM_MASTER_KEY is required"; exit 1)
	@[[ "$(LITELLM_MASTER_KEY)" == sk-* ]] || (echo "LITELLM_MASTER_KEY must start with sk-"; exit 1)
	@docker compose -f "$(LITELLM_VERIFY_COMPOSE_FILE)" up -d
	@echo "litellm verify stack is up: $(LITELLM_VERIFY_COMPOSE_FILE)"

litellm-verify-down:
	@docker compose -f "$(LITELLM_VERIFY_COMPOSE_FILE)" down
	@echo "litellm verify stack is down: $(LITELLM_VERIFY_COMPOSE_FILE)"

litellm-verify-status:
	@docker compose -f "$(LITELLM_VERIFY_COMPOSE_FILE)" ps

litellm-verify-logs:
	@docker compose -f "$(LITELLM_VERIFY_COMPOSE_FILE)" logs -f --tail=200

litellm-verify-run:
	@test -n "$(LITELLM_MASTER_KEY)" || (echo "LITELLM_MASTER_KEY is required"; exit 1)
	@[[ "$(LITELLM_MASTER_KEY)" == sk-* ]] || (echo "LITELLM_MASTER_KEY must start with sk-"; exit 1)
	@python3 core/litellm/verify_virtual_keys.py \
		--base-url "$(LITELLM_VERIFY_BASE_URL)" \
		--master-key "$(LITELLM_MASTER_KEY)" \
		--tenant-a "$(LITELLM_VERIFY_TENANT_A)" \
		--tenant-b "$(LITELLM_VERIFY_TENANT_B)" \
		--sleep-seconds 1.5

control-plane-up:
	@mkdir -p .tmp/control-plane .tmp/uv-cache
	@test -f "$(CONTROL_PLANE_ENV_FILE)" || (echo "missing env file: $(CONTROL_PLANE_ENV_FILE). copy docs/tenant-control-plane.env.example first"; exit 1)
	@set -a; source "$(CONTROL_PLANE_ENV_FILE)"; set +a; \
	for key in \
		CONTROL_PLANE_HOST CONTROL_PLANE_PORT PROJECT_ROOT REGISTRY_FILE WALLET_FILE \
		TRAEFIK_DASHBOARD_API_URL TRAEFIK_MAX_WAIT_SECONDS TRAEFIK_POLL_INTERVAL_SECONDS \
		PLATFORM_IMAGE_TAG PLATFORM_SERVICE_PORT PLATFORM_OPENAI_API_BASE PLATFORM_OPENAI_API_KEY PLATFORM_LOG_LEVEL \
		TENANT_KEY_PROVIDER; do \
		eval "value=\$${$$key}"; \
		if [[ -z "$$value" ]]; then \
			echo "missing required env key: $$key"; \
			exit 1; \
		fi; \
	done; \
	if [[ "$$TENANT_KEY_PROVIDER" == "skeleton" ]]; then \
		for key in TENANT_KEY_PREFIX TENANT_KEY_ENTROPY_BYTES; do \
			eval "value=\$${$$key}"; \
			if [[ -z "$$value" ]]; then \
				echo "missing required env key for skeleton provider: $$key"; \
				exit 1; \
			fi; \
		done; \
	elif [[ "$$TENANT_KEY_PROVIDER" == "litellm" ]]; then \
		for key in LITELLM_BASE_URL LITELLM_MASTER_KEY LITELLM_MODEL_NAME LITELLM_TIMEOUT_SECONDS; do \
			eval "value=\$${$$key}"; \
			if [[ -z "$$value" ]]; then \
				echo "missing required env key for litellm provider: $$key"; \
				exit 1; \
			fi; \
		done; \
	else \
		echo "invalid TENANT_KEY_PROVIDER=$$TENANT_KEY_PROVIDER (allowed: skeleton, litellm)"; \
		exit 1; \
	fi; \
	if [[ "$$CONTROL_PLANE_HOST" != "127.0.0.1" ]]; then \
		echo "CONTROL_PLANE_HOST must be 127.0.0.1 to keep control plane hidden behind Traefik"; \
		exit 1; \
	fi; \
	if [[ -f "$(CONTROL_PLANE_PID_FILE)" ]] && kill -0 "$$(cat "$(CONTROL_PLANE_PID_FILE)")" 2>/dev/null; then \
		echo "control-plane already running (pid=$$(cat "$(CONTROL_PLANE_PID_FILE)"))"; \
		exit 1; \
	fi; \
	cd "$$PROJECT_ROOT/cli"; \
	UV_CACHE_DIR="$$PROJECT_ROOT/.tmp/uv-cache" \
	PYTHONPATH="$$PROJECT_ROOT/cli/src" \
	nohup uv run clawos control-plane serve \
		--host "$$CONTROL_PLANE_HOST" \
		--port "$$CONTROL_PLANE_PORT" \
		--project-root "$$PROJECT_ROOT" \
		--registry-file "$$REGISTRY_FILE" \
		--wallet-file "$$WALLET_FILE" \
		--traefik-dashboard-api-url "$$TRAEFIK_DASHBOARD_API_URL" \
		--traefik-max-wait-seconds "$$TRAEFIK_MAX_WAIT_SECONDS" \
		--traefik-poll-interval-seconds "$$TRAEFIK_POLL_INTERVAL_SECONDS" \
		--platform-image-tag "$$PLATFORM_IMAGE_TAG" \
		--platform-service-port "$$PLATFORM_SERVICE_PORT" \
		--platform-openai-api-base "$$PLATFORM_OPENAI_API_BASE" \
		--platform-openai-api-key "$$PLATFORM_OPENAI_API_KEY" \
		--platform-log-level "$$PLATFORM_LOG_LEVEL" \
		--tenant-key-provider "$$TENANT_KEY_PROVIDER" \
		$${TENANT_KEY_PREFIX:+--tenant-key-prefix "$$TENANT_KEY_PREFIX"} \
		$${TENANT_KEY_ENTROPY_BYTES:+--tenant-key-entropy-bytes "$$TENANT_KEY_ENTROPY_BYTES"} \
		$${LITELLM_BASE_URL:+--litellm-base-url "$$LITELLM_BASE_URL"} \
		$${LITELLM_MASTER_KEY:+--litellm-master-key "$$LITELLM_MASTER_KEY"} \
		$${LITELLM_MODEL_NAME:+--litellm-model-name "$$LITELLM_MODEL_NAME"} \
		$${LITELLM_TIMEOUT_SECONDS:+--litellm-timeout-seconds "$$LITELLM_TIMEOUT_SECONDS"} \
		>"$$PROJECT_ROOT/$(CONTROL_PLANE_LOG_FILE)" 2>&1 & echo $$! >"$$PROJECT_ROOT/$(CONTROL_PLANE_PID_FILE)"; \
	echo "control-plane started: pid=$$(cat "$$PROJECT_ROOT/$(CONTROL_PLANE_PID_FILE)") log=$$PROJECT_ROOT/$(CONTROL_PLANE_LOG_FILE)"

control-plane-down:
	@if [[ ! -f "$(CONTROL_PLANE_PID_FILE)" ]]; then \
		echo "control-plane not running"; \
		exit 0; \
	fi
	@pid="$$(cat "$(CONTROL_PLANE_PID_FILE)")"; \
	if kill -0 "$$pid" 2>/dev/null; then \
		kill "$$pid"; \
		echo "control-plane stopped: pid=$$pid"; \
	else \
		echo "control-plane pid file exists but process not running: pid=$$pid"; \
	fi; \
	rm -f "$(CONTROL_PLANE_PID_FILE)"

control-plane-status:
	@if [[ -f "$(CONTROL_PLANE_PID_FILE)" ]] && kill -0 "$$(cat "$(CONTROL_PLANE_PID_FILE)")" 2>/dev/null; then \
		echo "running pid=$$(cat "$(CONTROL_PLANE_PID_FILE)")"; \
	else \
		echo "stopped"; \
	fi

control-plane-logs:
	@tail -f "$(CONTROL_PLANE_LOG_FILE)"

tenant-create-feishu:
	@test -n "$(TENANT_NAME)" || (echo "TENANT_NAME is required"; exit 1)
	@test -n "$(FEISHU_APP_ID)" || (echo "FEISHU_APP_ID is required"; exit 1)
	@test -n "$(FEISHU_APP_SECRET)" || (echo "FEISHU_APP_SECRET is required"; exit 1)
	@test -n "$(FEISHU_VERIFICATION_TOKEN)" || (echo "FEISHU_VERIFICATION_TOKEN is required"; exit 1)
	@test -f "$(CONTROL_PLANE_ENV_FILE)" || (echo "missing env file: $(CONTROL_PLANE_ENV_FILE)"; exit 1)
	@set -a; source "$(CONTROL_PLANE_ENV_FILE)"; set +a; \
	base_url="$${CONTROL_PLANE_API_BASE_URL:-$(CONTROL_PLANE_API_BASE_URL)}"; \
	base_url="$${base_url%/}"; \
	payload="$$(python3 -c 'import json,sys; tenant_name, app_id, app_secret, token = sys.argv[1:]; print(json.dumps({"tenant_name": tenant_name, "channel_type": "feishu", "channel_config": {"FEISHU_APP_ID": app_id, "FEISHU_APP_SECRET": app_secret, "FEISHU_VERIFICATION_TOKEN": token}}))' "$(TENANT_NAME)" "$(FEISHU_APP_ID)" "$(FEISHU_APP_SECRET)" "$(FEISHU_VERIFICATION_TOKEN)")"; \
	curl -sS -X POST "$$base_url/v1/tenants" \
		-H "Content-Type: application/json" \
		-d "$$payload" | tee "$(CONTROL_PLANE_LAST_CREATE_FILE)"; \
	echo ""

tenant-delete:
	@test -n "$(TENANT_ID)" || (echo "TENANT_ID is required"; exit 1)
	@test -f "$(CONTROL_PLANE_ENV_FILE)" || (echo "missing env file: $(CONTROL_PLANE_ENV_FILE)"; exit 1)
	@set -a; source "$(CONTROL_PLANE_ENV_FILE)"; set +a; \
	base_url="$${CONTROL_PLANE_API_BASE_URL:-$(CONTROL_PLANE_API_BASE_URL)}"; \
	base_url="$${base_url%/}"; \
	curl -sS -X DELETE "$$base_url/v1/tenants/$(TENANT_ID)" \
		-H "Content-Type: application/json" \
		-d '{}' | tee "$(CONTROL_PLANE_LAST_DELETE_FILE)"; \
	echo ""

wallet-topup:
	@test -n "$(TENANT_ID)" || (echo "TENANT_ID is required"; exit 1)
	@test -n "$(USER_ID)" || (echo "USER_ID is required"; exit 1)
	@test -n "$(AMOUNT)" || (echo "AMOUNT is required"; exit 1)
	@test -n "$(CURRENCY)" || (echo "CURRENCY is required"; exit 1)
	@test -n "$(IDEMPOTENCY_KEY)" || (echo "IDEMPOTENCY_KEY is required"; exit 1)
	@test -f "$(CONTROL_PLANE_ENV_FILE)" || (echo "missing env file: $(CONTROL_PLANE_ENV_FILE)"; exit 1)
	@set -a; source "$(CONTROL_PLANE_ENV_FILE)"; set +a; \
	base_url="$${CONTROL_PLANE_API_BASE_URL:-$(CONTROL_PLANE_API_BASE_URL)}"; \
	base_url="$${base_url%/}"; \
	payload="$$(python3 -c 'import json,sys; tenant_id,user_id,amount,currency,idempotency_key=sys.argv[1:6]; print(json.dumps({"user_id": user_id, "amount": amount, "currency": currency, "source": "manual_topup", "source_ref": f"{tenant_id}:{idempotency_key}", "request_id": f"topup-{idempotency_key}", "idempotency_key": idempotency_key, "metadata": {"operator": "makefile"}}))' "$(TENANT_ID)" "$(USER_ID)" "$(AMOUNT)" "$(CURRENCY)" "$(IDEMPOTENCY_KEY)")"; \
	curl -sS -X POST "$$base_url/v1/tenants/$(TENANT_ID)/wallet/topups" \
		-H "Content-Type: application/json" \
		-d "$$payload"; \
	echo ""

wallet-charge:
	@test -n "$(TENANT_ID)" || (echo "TENANT_ID is required"; exit 1)
	@test -n "$(USER_ID)" || (echo "USER_ID is required"; exit 1)
	@test -n "$(AMOUNT)" || (echo "AMOUNT is required"; exit 1)
	@test -n "$(CURRENCY)" || (echo "CURRENCY is required"; exit 1)
	@test -n "$(IDEMPOTENCY_KEY)" || (echo "IDEMPOTENCY_KEY is required"; exit 1)
	@test -n "$(REQUEST_ID)" || (echo "REQUEST_ID is required"; exit 1)
	@test -f "$(CONTROL_PLANE_ENV_FILE)" || (echo "missing env file: $(CONTROL_PLANE_ENV_FILE)"; exit 1)
	@set -a; source "$(CONTROL_PLANE_ENV_FILE)"; set +a; \
	base_url="$${CONTROL_PLANE_API_BASE_URL:-$(CONTROL_PLANE_API_BASE_URL)}"; \
	base_url="$${base_url%/}"; \
	payload="$$(python3 -c 'import json,sys; user_id,amount,currency,idempotency_key,request_id=sys.argv[1:6]; print(json.dumps({"user_id": user_id, "amount": amount, "currency": currency, "source": "litellm_proxy", "source_ref": request_id, "request_id": request_id, "idempotency_key": idempotency_key, "metadata": {"billing_mode": "postpay"}}))' "$(USER_ID)" "$(AMOUNT)" "$(CURRENCY)" "$(IDEMPOTENCY_KEY)" "$(REQUEST_ID)")"; \
	curl -sS -X POST "$$base_url/v1/tenants/$(TENANT_ID)/wallet/charges" \
		-H "Content-Type: application/json" \
		-d "$$payload"; \
	echo ""

wallet-balance:
	@test -n "$(TENANT_ID)" || (echo "TENANT_ID is required"; exit 1)
	@test -n "$(USER_ID)" || (echo "USER_ID is required"; exit 1)
	@test -f "$(CONTROL_PLANE_ENV_FILE)" || (echo "missing env file: $(CONTROL_PLANE_ENV_FILE)"; exit 1)
	@set -a; source "$(CONTROL_PLANE_ENV_FILE)"; set +a; \
	base_url="$${CONTROL_PLANE_API_BASE_URL:-$(CONTROL_PLANE_API_BASE_URL)}"; \
	base_url="$${base_url%/}"; \
	curl -sS "$$base_url/v1/tenants/$(TENANT_ID)/users/$(USER_ID)/balance"; \
	echo ""

tenant-verify:
	@test -n "$(TENANT_ID)" || (echo "TENANT_ID is required, example: make tenant-verify TENANT_ID=tenant-0001"; exit 1)
	@test -f "$(CONTROL_PLANE_ENV_FILE)" || (echo "missing env file: $(CONTROL_PLANE_ENV_FILE)"; exit 1)
	@set -a; source "$(CONTROL_PLANE_ENV_FILE)"; set +a; \
	echo "[1] tenant container status"; \
	docker ps --filter "name=clawos-$(TENANT_ID)" --format "table {{.Names}}\t{{.Status}}"; \
	echo "[2] traefik router status"; \
	curl -sS "$$TRAEFIK_DASHBOARD_API_URL/api/http/routers" | python3 -c 'import json,sys; tenant_id=sys.argv[1]; routers=json.load(sys.stdin); matched=[r for r in routers if tenant_id in str(r.get("name",""))]; (print("\n".join("{} status={} rule={}".format(r.get("name"), r.get("status"), r.get("rule")) for r in matched)) if matched else (_ for _ in ()).throw(SystemExit("router for {} not found".format(tenant_id))))' "$(TENANT_ID)"; \
	echo "[3] route interaction via traefik"; \
	code="$$(curl -sS -o .tmp/control-plane/tenant-route.out -w '%{http_code}' "http://127.0.0.1/tenant/$(TENANT_ID)")"; \
	if [[ "$$code" != "200" ]]; then \
		echo "route check failed: http_status=$$code"; \
		cat .tmp/control-plane/tenant-route.out; \
		exit 1; \
	fi; \
	head -n 20 .tmp/control-plane/tenant-route.out
