SHELL := /bin/bash

CONTROL_PLANE_ENV_FILE ?= .tmp/control-plane.env
CONTROL_PLANE_PID_FILE := .tmp/control-plane/control-plane.pid
CONTROL_PLANE_LOG_FILE := .tmp/control-plane/control-plane.log
CONTROL_PLANE_LAST_CREATE_FILE := .tmp/control-plane/last-create.json
TRAEFIK_COMPOSE_FILE ?= core/traefik/compose.multitenant-demo.yaml
TRAEFIK_LOG_DIR ?= core/traefik/logs
TRAEFIK_DASHBOARD_API_URL ?= http://127.0.0.1:8080

.PHONY: traefik-up traefik-down traefik-restart traefik-status traefik-logs traefik-routes control-plane-up control-plane-down control-plane-status control-plane-logs tenant-create-feishu tenant-verify

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

control-plane-up:
	@mkdir -p .tmp/control-plane .tmp/uv-cache
	@test -f "$(CONTROL_PLANE_ENV_FILE)" || (echo "missing env file: $(CONTROL_PLANE_ENV_FILE). copy docs/tenant-control-plane.env.example first"; exit 1)
	@set -a; source "$(CONTROL_PLANE_ENV_FILE)"; set +a; \
	for key in \
		CONTROL_PLANE_HOST CONTROL_PLANE_PORT PROJECT_ROOT REGISTRY_FILE \
		TRAEFIK_DASHBOARD_API_URL TRAEFIK_MAX_WAIT_SECONDS TRAEFIK_POLL_INTERVAL_SECONDS \
		PLATFORM_IMAGE_TAG PLATFORM_SERVICE_PORT PLATFORM_OPENAI_API_BASE PLATFORM_OPENAI_API_KEY PLATFORM_LOG_LEVEL \
		TENANT_KEY_PREFIX TENANT_KEY_ENTROPY_BYTES; do \
		eval "value=\$${$$key}"; \
		if [[ -z "$$value" ]]; then \
			echo "missing required env key: $$key"; \
			exit 1; \
		fi; \
	done; \
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
		--traefik-dashboard-api-url "$$TRAEFIK_DASHBOARD_API_URL" \
		--traefik-max-wait-seconds "$$TRAEFIK_MAX_WAIT_SECONDS" \
		--traefik-poll-interval-seconds "$$TRAEFIK_POLL_INTERVAL_SECONDS" \
		--platform-image-tag "$$PLATFORM_IMAGE_TAG" \
		--platform-service-port "$$PLATFORM_SERVICE_PORT" \
		--platform-openai-api-base "$$PLATFORM_OPENAI_API_BASE" \
		--platform-openai-api-key "$$PLATFORM_OPENAI_API_KEY" \
		--platform-log-level "$$PLATFORM_LOG_LEVEL" \
		--tenant-key-prefix "$$TENANT_KEY_PREFIX" \
		--tenant-key-entropy-bytes "$$TENANT_KEY_ENTROPY_BYTES" \
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
	payload="$$(python3 -c 'import json,sys; tenant_name, app_id, app_secret, token = sys.argv[1:]; print(json.dumps({"tenant_name": tenant_name, "channel_type": "feishu", "channel_config": {"FEISHU_APP_ID": app_id, "FEISHU_APP_SECRET": app_secret, "FEISHU_VERIFICATION_TOKEN": token}}))' "$(TENANT_NAME)" "$(FEISHU_APP_ID)" "$(FEISHU_APP_SECRET)" "$(FEISHU_VERIFICATION_TOKEN)")"; \
	curl -sS -X POST "http://$$CONTROL_PLANE_HOST:$$CONTROL_PLANE_PORT/v1/tenants" \
		-H "Content-Type: application/json" \
		-d "$$payload" | tee "$(CONTROL_PLANE_LAST_CREATE_FILE)"; \
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
