import json
import logging
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clawos_cli.application.tenant_provision_dto import (
    TenantDeleteRequest,
    TenantProvisionRequest,
    UserWalletMutationRequest,
)
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
            try:
                if self.path == "/v1/tenants":
                    payload = self._read_json_body()
                    request = self._to_provision_request(payload=payload)
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
                    return

                topup_route = self._match_wallet_route(path=self.path, action="topups")
                if topup_route is not None:
                    payload = self._read_json_body()
                    request = self._to_wallet_request(tenant_id=topup_route["tenant_id"], payload=payload)
                    result = tenant_provision_service.topup_user_wallet(request=request)
                    self._write_json(
                        status=HTTPStatus.OK,
                        payload={
                            "tenant_id": result.tenant_id,
                            "user_id": result.user_id,
                            "event_type": result.event_type,
                            "delta": result.delta,
                            "balance_after": result.balance_after,
                            "currency": result.currency,
                            "ledger_id": result.ledger_id,
                            "idempotent_replay": result.idempotent_replay,
                        },
                    )
                    return

                charge_route = self._match_wallet_route(path=self.path, action="charges")
                if charge_route is not None:
                    payload = self._read_json_body()
                    request = self._to_wallet_request(tenant_id=charge_route["tenant_id"], payload=payload)
                    result = tenant_provision_service.charge_user_wallet(request=request)
                    self._write_json(
                        status=HTTPStatus.OK,
                        payload={
                            "tenant_id": result.tenant_id,
                            "user_id": result.user_id,
                            "event_type": result.event_type,
                            "delta": result.delta,
                            "balance_after": result.balance_after,
                            "currency": result.currency,
                            "ledger_id": result.ledger_id,
                            "idempotent_replay": result.idempotent_replay,
                        },
                    )
                    return

                self._write_json(status=HTTPStatus.NOT_FOUND, payload={"error": "not found"})
            except ClawOSError as exc:
                logger.warning("tenant_control_plane_request_error code=%s message=%s", exc.code.value, exc.message)
                status = HTTPStatus.BAD_REQUEST
                if exc.code in (
                    ErrorCode.CONTAINER_START_FAILED,
                    ErrorCode.CONTAINER_STOP_FAILED,
                    ErrorCode.HEALTHCHECK_TIMEOUT,
                    ErrorCode.TRAEFIK_ROUTER_NOT_READY,
                    ErrorCode.TRAEFIK_ROUTER_NOT_REMOVED,
                ):
                    status = HTTPStatus.BAD_GATEWAY
                if exc.code in (
                    ErrorCode.TENANT_NOT_FOUND,
                    ErrorCode.USER_BALANCE_NOT_FOUND,
                ):
                    status = HTTPStatus.NOT_FOUND
                if exc.code == ErrorCode.LLM_BUDGET_EXHAUSTED:
                    status = HTTPStatus.PAYMENT_REQUIRED
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

        def do_DELETE(self) -> None:  # noqa: N802
            tenant_id = self._extract_tenant_id(path=self.path)
            if tenant_id is None:
                self._write_json(status=HTTPStatus.NOT_FOUND, payload={"error": "not found"})
                return
            try:
                payload = self._read_json_body()
                if payload:
                    self._write_json(
                        status=HTTPStatus.BAD_REQUEST,
                        payload={
                            "error_code": ErrorCode.TENANT_DELETE_REQUEST_INVALID.value,
                            "message": "delete request body must be empty",
                        },
                    )
                    return
                result = tenant_provision_service.delete_tenant(
                    request=TenantDeleteRequest(
                        tenant_id=tenant_id,
                        requested_at=datetime.now(tz=timezone.utc),
                    )
                )
                self._write_json(
                    status=HTTPStatus.OK,
                    payload={
                        "tenant_id": result.tenant_id,
                        "status": result.status,
                    },
                )
            except ClawOSError as exc:
                logger.warning("tenant_control_plane_request_error code=%s message=%s", exc.code.value, exc.message)
                status = HTTPStatus.BAD_REQUEST
                if exc.code in (
                    ErrorCode.CONTAINER_STOP_FAILED,
                    ErrorCode.HEALTHCHECK_TIMEOUT,
                    ErrorCode.TRAEFIK_ROUTER_NOT_REMOVED,
                ):
                    status = HTTPStatus.BAD_GATEWAY
                if exc.code == ErrorCode.TENANT_NOT_FOUND:
                    status = HTTPStatus.NOT_FOUND
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

            balance_route = self._match_balance_route(path=self.path)
            if balance_route is not None:
                try:
                    result = tenant_provision_service.get_user_balance(
                        tenant_id=balance_route["tenant_id"],
                        user_id=balance_route["user_id"],
                    )
                    self._write_json(
                        status=HTTPStatus.OK,
                        payload={
                            "tenant_id": result.tenant_id,
                            "user_id": result.user_id,
                            "balance": result.balance,
                            "currency": result.currency,
                            "updated_at": result.updated_at.isoformat(),
                        },
                    )
                    return
                except ClawOSError as exc:
                    status = HTTPStatus.BAD_REQUEST
                    if exc.code in (ErrorCode.TENANT_NOT_FOUND, ErrorCode.USER_BALANCE_NOT_FOUND):
                        status = HTTPStatus.NOT_FOUND
                    self._write_json(
                        status=status,
                        payload={"error_code": exc.code.value, "message": exc.message},
                    )
                    return

            tenant_id = self._extract_tenant_id(path=self.path)
            if tenant_id is None:
                self._write_json(status=HTTPStatus.NOT_FOUND, payload={"error": "not found"})
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
            if content_length == 0:
                return {}
            raw_body = self.rfile.read(content_length)
            body = raw_body.decode("utf-8")
            parsed = json.loads(body)
            if not isinstance(parsed, dict):
                raise ClawOSError(
                    code=ErrorCode.TENANT_CREATE_REQUEST_INVALID,
                    message="request body must be a json object",
                )
            return parsed

        def _to_provision_request(self, payload: dict[str, object]) -> TenantProvisionRequest:
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

        def _to_wallet_request(self, tenant_id: str, payload: dict[str, object]) -> UserWalletMutationRequest:
            required_str_fields = (
                "user_id",
                "amount",
                "currency",
                "source",
                "source_ref",
                "request_id",
                "idempotency_key",
            )
            values: dict[str, str] = {}
            for key in required_str_fields:
                raw = payload.get(key)
                if not isinstance(raw, str):
                    raise ClawOSError(
                        code=ErrorCode.WALLET_REQUEST_INVALID,
                        message=f"{key} must be string",
                    )
                values[key] = raw

            metadata_raw = payload.get("metadata")
            if not isinstance(metadata_raw, dict):
                raise ClawOSError(
                    code=ErrorCode.WALLET_REQUEST_INVALID,
                    message="metadata must be object",
                )
            metadata: dict[str, str] = {}
            for key, value in metadata_raw.items():
                if not isinstance(key, str):
                    raise ClawOSError(
                        code=ErrorCode.WALLET_REQUEST_INVALID,
                        message="metadata keys must be string",
                    )
                metadata[key] = json.dumps(value, ensure_ascii=True) if not isinstance(value, str) else value

            return UserWalletMutationRequest(
                tenant_id=tenant_id,
                user_id=values["user_id"],
                amount=values["amount"],
                currency=values["currency"],
                source=values["source"],
                source_ref=values["source_ref"],
                request_id=values["request_id"],
                idempotency_key=values["idempotency_key"],
                metadata=metadata,
                requested_at=datetime.now(tz=timezone.utc),
            )

        def _extract_tenant_id(self, path: str) -> str | None:
            prefix = "/v1/tenants/"
            if not path.startswith(prefix):
                return None
            remainder = path[len(prefix) :]
            if remainder.strip() == "":
                return None
            if "/" in remainder:
                return None
            return remainder

        def _match_wallet_route(self, path: str, action: str) -> dict[str, str] | None:
            segments = [segment for segment in path.split("/") if segment != ""]
            if len(segments) != 5:
                return None
            if segments[0] != "v1" or segments[1] != "tenants":
                return None
            if segments[3] != "wallet":
                return None
            if segments[4] != action:
                return None
            return {"tenant_id": segments[2]}

        def _match_balance_route(self, path: str) -> dict[str, str] | None:
            segments = [segment for segment in path.split("/") if segment != ""]
            if len(segments) != 6:
                return None
            if segments[0] != "v1" or segments[1] != "tenants":
                return None
            if segments[3] != "users" or segments[5] != "balance":
                return None
            return {"tenant_id": segments[2], "user_id": segments[4]}

        def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return TenantControlPlaneHandler
