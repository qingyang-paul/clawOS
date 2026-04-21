import logging
import json
from pathlib import Path
import threading
from decimal import Decimal, InvalidOperation

from clawos_cli.application.tenant_provision_dto import (
    TenantDeleteRequest,
    TenantDeleteResult,
    TenantProvisionPlatformConfig,
    TenantProvisionRequest,
    TenantProvisionResult,
    UserBalanceResult,
    UserWalletMutationRequest,
    UserWalletMutationResult,
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
from clawos_cli.infrastructure.user_wallet_gateway import UserWalletGateway


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
        user_wallet_gateway: UserWalletGateway,
        litellm_topup_fx_rates_to_usd: dict[str, str],
        litellm_topup_sync_state_file: Path,
        logger: logging.Logger,
    ) -> None:
        self._project_root = project_root
        self._platform_config = platform_config
        self._tenant_registry_gateway = tenant_registry_gateway
        self._tenant_runtime_gateway = tenant_runtime_gateway
        self._traefik_gateway = traefik_gateway
        self._tenant_key_provider = tenant_key_provider
        self._user_wallet_gateway = user_wallet_gateway
        self._litellm_topup_fx_rates_to_usd = {
            key.upper(): value for key, value in litellm_topup_fx_rates_to_usd.items()
        }
        self._litellm_topup_sync_state_file = litellm_topup_sync_state_file
        self._litellm_topup_sync_state_lock = threading.Lock()
        self._litellm_topup_sync_state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._litellm_topup_sync_state_file.exists():
            self._write_litellm_topup_sync_state(payload={"applied_topup_idempotency_keys": []})
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

    def delete_tenant(self, request: TenantDeleteRequest) -> TenantDeleteResult:
        tenant = self._tenant_registry_gateway.get_tenant(tenant_id=request.tenant_id)
        if tenant.status == "DELETED":
            return TenantDeleteResult(tenant_id=tenant.tenant_id, status=tenant.status)

        if tenant.status not in ("RUNNING", "ERROR"):
            raise ClawOSError(
                code=ErrorCode.TENANT_STATUS_INVALID,
                message=f"tenant cannot be deleted from status={tenant.status}",
            )

        self._logger.info("tenant_delete_start tenant_id=%s", tenant.tenant_id)
        self._tenant_registry_gateway.update_tenant_status(
            tenant_id=tenant.tenant_id,
            status="DELETING",
            updated_at=request.requested_at,
            reason_code="",
        )

        tenants_root = self._project_root / "tenants"
        runtime_env = self._tenant_runtime_gateway.read_tenant_env(
            tenants_root=tenants_root,
            tenant_id=tenant.tenant_id,
        )
        tenant_openai_api_key = runtime_env.get("OPENAI_API_KEY", "")

        try:
            self._tenant_runtime_gateway.stop_tenant(
                tenants_root=tenants_root,
                tenant_id=tenant.tenant_id,
            )
            self._traefik_gateway.wait_router_removed(router_name=tenant.tenant_id)
            self._tenant_key_provider.revoke_openai_api_key(
                tenant_id=tenant.tenant_id,
                api_key=tenant_openai_api_key,
            )
            self._tenant_runtime_gateway.remove_tenant_files(
                tenants_root=tenants_root,
                tenant_id=tenant.tenant_id,
            )
        except ClawOSError as exc:
            self._tenant_registry_gateway.update_tenant_status(
                tenant_id=tenant.tenant_id,
                status="ERROR",
                updated_at=request.requested_at,
                reason_code=exc.code.value,
            )
            self._logger.error(
                "tenant_delete_failed tenant_id=%s reason_code=%s message=%s",
                tenant.tenant_id,
                exc.code.value,
                exc.message,
            )
            raise

        self._tenant_registry_gateway.update_tenant_status(
            tenant_id=tenant.tenant_id,
            status="DELETED",
            updated_at=request.requested_at,
            reason_code="",
        )
        self._logger.info("tenant_delete_done tenant_id=%s", tenant.tenant_id)
        return TenantDeleteResult(tenant_id=tenant.tenant_id, status="DELETED")

    def topup_user_wallet(self, request: UserWalletMutationRequest) -> UserWalletMutationResult:
        tenant = self._tenant_registry_gateway.get_tenant(tenant_id=request.tenant_id)
        self._validate_tenant_wallet_mutation_status(tenant_status=tenant.status, tenant_id=tenant.tenant_id)
        wallet_amount = request.amount
        wallet_currency = request.currency
        wallet_metadata = dict(request.metadata)
        if self._tenant_key_provider.provider_name() == "litellm":
            converted = self._convert_topup_to_usd(amount=request.amount, source_currency=request.currency)
            wallet_amount = converted["amount_usd"]
            wallet_currency = "USD"
            wallet_metadata["topup_input_amount"] = request.amount
            wallet_metadata["topup_input_currency"] = request.currency.upper()
            wallet_metadata["topup_fx_rate_to_usd"] = converted["fx_rate_to_usd"]

        ledger = self._user_wallet_gateway.topup(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            amount=wallet_amount,
            currency=wallet_currency,
            source=request.source,
            source_ref=request.source_ref,
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            metadata=wallet_metadata,
            occurred_at=request.requested_at,
            created_at=request.requested_at,
        )
        if self._tenant_key_provider.provider_name() == "litellm":
            self._sync_litellm_budget_from_topup(
                tenant_id=request.tenant_id,
                idempotency_key=request.idempotency_key,
                budget_delta_usd=wallet_amount,
            )
        self._logger.info(
            "wallet_topup tenant_id=%s user_id=%s delta=%s balance_after=%s idempotent_replay=%s",
            ledger.tenant_id,
            ledger.user_id,
            ledger.delta,
            ledger.balance_after,
            ledger.idempotent_replay,
        )
        return UserWalletMutationResult(
            tenant_id=ledger.tenant_id,
            user_id=ledger.user_id,
            event_type=ledger.event_type,
            delta=ledger.delta,
            balance_after=ledger.balance_after,
            currency=ledger.currency,
            ledger_id=ledger.ledger_id,
            idempotent_replay=ledger.idempotent_replay,
        )

    def charge_user_wallet(self, request: UserWalletMutationRequest) -> UserWalletMutationResult:
        tenant = self._tenant_registry_gateway.get_tenant(tenant_id=request.tenant_id)
        self._validate_tenant_wallet_mutation_status(tenant_status=tenant.status, tenant_id=tenant.tenant_id)
        ledger = self._user_wallet_gateway.charge(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            amount=request.amount,
            currency=request.currency,
            source=request.source,
            source_ref=request.source_ref,
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            metadata=request.metadata,
            occurred_at=request.requested_at,
            created_at=request.requested_at,
        )
        self._logger.info(
            "wallet_charge tenant_id=%s user_id=%s delta=%s balance_after=%s idempotent_replay=%s",
            ledger.tenant_id,
            ledger.user_id,
            ledger.delta,
            ledger.balance_after,
            ledger.idempotent_replay,
        )
        return UserWalletMutationResult(
            tenant_id=ledger.tenant_id,
            user_id=ledger.user_id,
            event_type=ledger.event_type,
            delta=ledger.delta,
            balance_after=ledger.balance_after,
            currency=ledger.currency,
            ledger_id=ledger.ledger_id,
            idempotent_replay=ledger.idempotent_replay,
        )

    def get_user_balance(self, tenant_id: str, user_id: str) -> UserBalanceResult:
        tenant = self._tenant_registry_gateway.get_tenant(tenant_id=tenant_id)
        self._validate_tenant_wallet_mutation_status(tenant_status=tenant.status, tenant_id=tenant.tenant_id)
        balance = self._user_wallet_gateway.get_user_balance(tenant_id=tenant_id, user_id=user_id)
        return UserBalanceResult(
            tenant_id=balance.tenant_id,
            user_id=balance.user_id,
            balance=balance.balance,
            currency=balance.currency,
            updated_at=balance.updated_at,
        )

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

    def _validate_tenant_wallet_mutation_status(self, tenant_status: str, tenant_id: str) -> None:
        if tenant_status in ("DELETING", "DELETED"):
            raise ClawOSError(
                code=ErrorCode.TENANT_STATUS_INVALID,
                message=(
                    "wallet operation is blocked for tenant status "
                    f"tenant_id={tenant_id} status={tenant_status}"
                ),
            )

    def _convert_topup_to_usd(self, amount: str, source_currency: str) -> dict[str, str]:
        source_currency_upper = source_currency.upper()
        rate_raw = self._litellm_topup_fx_rates_to_usd.get(source_currency_upper)
        if rate_raw is None:
            raise ClawOSError(
                code=ErrorCode.TOPUP_EXCHANGE_RATE_MISSING,
                message=f"missing exchange rate for currency={source_currency_upper}",
            )
        try:
            rate = Decimal(rate_raw)
            amount_decimal = Decimal(amount)
        except InvalidOperation as exc:
            raise ClawOSError(
                code=ErrorCode.WALLET_REQUEST_INVALID,
                message=(
                    "invalid topup amount or fx rate "
                    f"amount={amount} fx_rate_to_usd={rate_raw} currency={source_currency_upper}"
                ),
            ) from exc
        if amount_decimal <= Decimal("0") or rate <= Decimal("0"):
            raise ClawOSError(
                code=ErrorCode.WALLET_REQUEST_INVALID,
                message=(
                    "topup amount and fx rate must be positive "
                    f"amount={amount_decimal} fx_rate_to_usd={rate} currency={source_currency_upper}"
                ),
            )
        amount_usd = (amount_decimal * rate).quantize(Decimal("0.000001"))
        return {
            "amount_usd": format(amount_usd, "f"),
            "fx_rate_to_usd": format(rate.quantize(Decimal("0.000001")), "f"),
        }

    def _sync_litellm_budget_from_topup(
        self,
        tenant_id: str,
        idempotency_key: str,
        budget_delta_usd: str,
    ) -> None:
        sync_state = self._read_litellm_topup_sync_state()
        applied = set(sync_state["applied_topup_idempotency_keys"])
        if idempotency_key in applied:
            return

        tenants_root = self._project_root / "tenants"
        runtime_env = self._tenant_runtime_gateway.read_tenant_env(
            tenants_root=tenants_root,
            tenant_id=tenant_id,
        )
        tenant_openai_api_key = runtime_env.get("OPENAI_API_KEY", "").strip()
        if tenant_openai_api_key == "":
            raise ClawOSError(
                code=ErrorCode.PLATFORM_CONFIG_MISSING,
                message=f"tenant openai api key not found in tenant runtime env tenant_id={tenant_id}",
            )
        self._tenant_key_provider.sync_topup_budget_usd(
            tenant_id=tenant_id,
            api_key=tenant_openai_api_key,
            budget_delta_usd=budget_delta_usd,
        )
        applied.add(idempotency_key)
        self._write_litellm_topup_sync_state(
            payload={"applied_topup_idempotency_keys": sorted(applied)}
        )

    def _read_litellm_topup_sync_state(self) -> dict[str, list[str]]:
        with self._litellm_topup_sync_state_lock:
            raw = json.loads(self._litellm_topup_sync_state_file.read_text(encoding="utf-8"))
        keys = raw.get("applied_topup_idempotency_keys")
        if isinstance(keys, list):
            return {"applied_topup_idempotency_keys": [str(item) for item in keys]}
        return {"applied_topup_idempotency_keys": []}

    def _write_litellm_topup_sync_state(self, payload: dict[str, list[str]]) -> None:
        with self._litellm_topup_sync_state_lock:
            self._litellm_topup_sync_state_file.write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )
