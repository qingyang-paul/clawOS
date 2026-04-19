import logging
from pathlib import Path

from clawos_cli.application.tenant_provision_dto import (
    TenantProvisionPlatformConfig,
    TenantProvisionRequest,
    TenantProvisionResult,
)
from clawos_cli.domain.error_codes import ErrorCode
from clawos_cli.domain.exceptions import ClawOSError
from clawos_cli.infrastructure.tenant_key_provider import TenantKeyProvider
from clawos_cli.infrastructure.tenant_registry_gateway import (
    TenantProvisionJobRecord,
    TenantRegistryGateway,
    TenantRegistryRecord,
)
from clawos_cli.infrastructure.tenant_runtime_gateway import TenantRuntimeGateway, TenantRuntimeSpec
from clawos_cli.infrastructure.traefik_gateway import TraefikGateway


CHANNEL_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "feishu": ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_VERIFICATION_TOKEN"),
}


class TenantProvisionService:
    def __init__(
        self,
        project_root: Path,
        platform_config: TenantProvisionPlatformConfig,
        tenant_registry_gateway: TenantRegistryGateway,
        tenant_runtime_gateway: TenantRuntimeGateway,
        traefik_gateway: TraefikGateway,
        tenant_key_provider: TenantKeyProvider,
        logger: logging.Logger,
    ) -> None:
        self._project_root = project_root
        self._platform_config = platform_config
        self._tenant_registry_gateway = tenant_registry_gateway
        self._tenant_runtime_gateway = tenant_runtime_gateway
        self._traefik_gateway = traefik_gateway
        self._tenant_key_provider = tenant_key_provider
        self._logger = logger

    def provision(self, request: TenantProvisionRequest) -> TenantProvisionResult:
        self._validate_request(request=request)

        tenant_id = self._tenant_registry_gateway.allocate_tenant_id()
        route_prefix = f"/tenant/{tenant_id}"
        tenant_openai_api_key = self._tenant_key_provider.issue_openai_api_key(tenant_id=tenant_id)
        job_id = request.requested_at.strftime(f"job-%Y%m%dT%H%M%SZ-{tenant_id}")
        now = request.requested_at

        self._logger.info(
            "tenant_provision_start tenant_id=%s route_prefix=%s channel_type=%s",
            tenant_id,
            route_prefix,
            request.channel_type,
        )
        self._tenant_registry_gateway.insert_tenant(
            record=TenantRegistryRecord(
                tenant_id=tenant_id,
                tenant_name=request.tenant_name,
                channel_type=request.channel_type,
                status="CREATING",
                route_prefix=route_prefix,
                image_tag=self._platform_config.image_tag,
                created_at=now,
                updated_at=now,
                reason_code="",
            )
        )
        self._tenant_registry_gateway.insert_job(
            record=TenantProvisionJobRecord(
                job_id=job_id,
                tenant_id=tenant_id,
                status="CREATING",
                created_at=now,
                updated_at=now,
                reason_code="",
            )
        )

        try:
            compose_file = self._tenant_runtime_gateway.write_tenant_files(
                tenants_root=self._project_root / "tenants",
                runtime_spec=TenantRuntimeSpec(
                    tenant_id=tenant_id,
                    tenant_name=request.tenant_name,
                    image_tag=self._platform_config.image_tag,
                    service_port=self._platform_config.service_port,
                    route_prefix=route_prefix,
                    openai_api_base=self._platform_config.openai_api_base,
                    openai_api_key=tenant_openai_api_key,
                    log_level=self._platform_config.log_level,
                    channel_type=request.channel_type,
                    channel_config=request.channel_config,
                ),
            )
            self._tenant_runtime_gateway.start_tenant(compose_file=compose_file)
            self._traefik_gateway.wait_router_ready(router_name=tenant_id)
        except ClawOSError as exc:
            self._tenant_registry_gateway.update_tenant_status(
                tenant_id=tenant_id,
                status="ERROR",
                updated_at=now,
                reason_code=exc.code.value,
            )
            self._tenant_registry_gateway.update_job_status(
                job_id=job_id,
                status="ERROR",
                updated_at=now,
                reason_code=exc.code.value,
            )
            self._logger.error(
                "tenant_provision_failed tenant_id=%s reason_code=%s message=%s",
                tenant_id,
                exc.code.value,
                exc.message,
            )
            raise

        self._tenant_registry_gateway.update_tenant_status(
            tenant_id=tenant_id,
            status="RUNNING",
            updated_at=now,
            reason_code="",
        )
        self._tenant_registry_gateway.update_job_status(
            job_id=job_id,
            status="DONE",
            updated_at=now,
            reason_code="",
        )
        self._logger.info("tenant_provision_done tenant_id=%s job_id=%s", tenant_id, job_id)
        return TenantProvisionResult(
            tenant_id=tenant_id,
            route_prefix=route_prefix,
            image_tag=self._platform_config.image_tag,
            status="RUNNING",
            job_id=job_id,
        )

    def get_tenant(self, tenant_id: str) -> TenantRegistryRecord:
        return self._tenant_registry_gateway.get_tenant(tenant_id=tenant_id)

    def _validate_request(self, request: TenantProvisionRequest) -> None:
        normalized_channel_type = request.channel_type.lower()
        if normalized_channel_type not in CHANNEL_REQUIRED_KEYS:
            raise ClawOSError(
                code=ErrorCode.TENANT_CHANNEL_NOT_SUPPORTED,
                message=f"unsupported channel_type: {request.channel_type}",
            )

        missing_keys = [
            key
            for key in CHANNEL_REQUIRED_KEYS[normalized_channel_type]
            if request.channel_config.get(key, "").strip() == ""
        ]
        if missing_keys:
            raise ClawOSError(
                code=ErrorCode.MISSING_ENV_KEY,
                message=f"missing required channel config keys: {','.join(missing_keys)}",
            )

        if request.tenant_name.strip() == "":
            raise ClawOSError(
                code=ErrorCode.TENANT_CREATE_REQUEST_INVALID,
                message="tenant_name is required",
            )
