import json
import logging
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clawos_cli.application.tenant_provision_dto import TenantProvisionRequest
from clawos_cli.application.tenant_provision_service import TenantProvisionService
from clawos_cli.domain.error_codes import ErrorCode
from clawos_cli.domain.exceptions import ClawOSError


FORBIDDEN_FRONTEND_KEYS = {
    "tenant_id",
    "route_prefix",
    "image_tag",
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
    "LOG_LEVEL",
}


def serve_tenant_control_plane(
    host: str,
    port: int,
    tenant_provision_service: TenantProvisionService,
    logger: logging.Logger,
) -> None:
    handler = _build_handler(tenant_provision_service=tenant_provision_service, logger=logger)
    server = ThreadingHTTPServer((host, port), handler)
    logger.info("tenant_control_plane_start host=%s port=%s", host, port)
    server.serve_forever()


def _build_handler(
    tenant_provision_service: TenantProvisionService,
    logger: logging.Logger,
) -> type[BaseHTTPRequestHandler]:
    class TenantControlPlaneHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/tenants":
                self._write_json(status=HTTPStatus.NOT_FOUND, payload={"error": "not found"})
                return

            try:
                payload = self._read_json_body()
                request = self._to_request(payload=payload)
                result = tenant_provision_service.provision(request=request)
                self._write_json(
                    status=HTTPStatus.CREATED,
                    payload={
                        "tenant_id": result.tenant_id,
                        "route_prefix": result.route_prefix,
                        "image_tag": result.image_tag,
                        "status": result.status,
                        "job_id": result.job_id,
                    },
                )
            except ClawOSError as exc:
                logger.warning("tenant_control_plane_request_error code=%s message=%s", exc.code.value, exc.message)
                status = HTTPStatus.BAD_REQUEST
                if exc.code in (
                    ErrorCode.CONTAINER_START_FAILED,
                    ErrorCode.HEALTHCHECK_TIMEOUT,
                    ErrorCode.TRAEFIK_ROUTER_NOT_READY,
                ):
                    status = HTTPStatus.BAD_GATEWAY
                self._write_json(
                    status=status,
                    payload={
                        "error_code": exc.code.value,
                        "message": exc.message,
                    },
                )
            except json.JSONDecodeError as exc:
                self._write_json(
                    status=HTTPStatus.BAD_REQUEST,
                    payload={
                        "error_code": ErrorCode.TENANT_CREATE_REQUEST_INVALID.value,
                        "message": f"invalid json body: {exc}",
                    },
                )

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._write_json(status=HTTPStatus.OK, payload={"status": "ok"})
                return
            if not self.path.startswith("/v1/tenants/"):
                self._write_json(status=HTTPStatus.NOT_FOUND, payload={"error": "not found"})
                return
            tenant_id = self.path.split("/v1/tenants/", 1)[1]
            if tenant_id.strip() == "":
                self._write_json(status=HTTPStatus.BAD_REQUEST, payload={"error": "tenant_id is required"})
                return
            try:
                tenant = tenant_provision_service.get_tenant(tenant_id=tenant_id)
            except ClawOSError as exc:
                self._write_json(
                    status=HTTPStatus.NOT_FOUND,
                    payload={"error_code": exc.code.value, "message": exc.message},
                )
                return

            self._write_json(
                status=HTTPStatus.OK,
                payload={
                    "tenant_id": tenant.tenant_id,
                    "tenant_name": tenant.tenant_name,
                    "channel_type": tenant.channel_type,
                    "status": tenant.status,
                    "route_prefix": tenant.route_prefix,
                    "image_tag": tenant.image_tag,
                    "reason_code": tenant.reason_code,
                    "created_at": tenant.created_at.isoformat(),
                    "updated_at": tenant.updated_at.isoformat(),
                },
            )

        def log_message(self, format: str, *args: object) -> None:
            logger.info("tenant_control_plane_access " + format, *args)

        def _read_json_body(self) -> dict[str, object]:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            body = raw_body.decode("utf-8")
            parsed = json.loads(body)
            if not isinstance(parsed, dict):
                raise ClawOSError(
                    code=ErrorCode.TENANT_CREATE_REQUEST_INVALID,
                    message="request body must be a json object",
                )
            return parsed

        def _to_request(self, payload: dict[str, object]) -> TenantProvisionRequest:
            forbidden = [key for key in FORBIDDEN_FRONTEND_KEYS if key in payload]
            if forbidden:
                raise ClawOSError(
                    code=ErrorCode.TENANT_CREATE_REQUEST_INVALID,
                    message=f"frontend cannot set backend-managed fields: {','.join(sorted(forbidden))}",
                )

            tenant_name_raw = payload.get("tenant_name")
            channel_type_raw = payload.get("channel_type")
            channel_config_raw = payload.get("channel_config")
            if not isinstance(tenant_name_raw, str):
                raise ClawOSError(
                    code=ErrorCode.TENANT_CREATE_REQUEST_INVALID,
                    message="tenant_name must be string",
                )
            if not isinstance(channel_type_raw, str):
                raise ClawOSError(
                    code=ErrorCode.TENANT_CREATE_REQUEST_INVALID,
                    message="channel_type must be string",
                )
            if not isinstance(channel_config_raw, dict):
                raise ClawOSError(
                    code=ErrorCode.TENANT_CREATE_REQUEST_INVALID,
                    message="channel_config must be object",
                )
            channel_config: dict[str, str] = {}
            for key, value in channel_config_raw.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    raise ClawOSError(
                        code=ErrorCode.TENANT_CREATE_REQUEST_INVALID,
                        message="channel_config keys and values must be string",
                    )
                channel_config[key] = value

            return TenantProvisionRequest(
                tenant_name=tenant_name_raw,
                channel_type=channel_type_raw,
                channel_config=channel_config,
                requested_at=datetime.now(tz=timezone.utc),
            )

        def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return TenantControlPlaneHandler
