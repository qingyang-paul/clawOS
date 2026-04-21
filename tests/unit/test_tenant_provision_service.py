import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from clawos_cli.application.tenant_provision_dto import (
    TenantProvisionPlatformConfig,
    TenantProvisionRequest,
    UserWalletMutationRequest,
)
from clawos_cli.application.tenant_provision_service import TenantProvisionService
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
from clawos_cli.infrastructure.user_wallet_gateway import (
    UserBalanceRecord,
    UserLedgerRecord,
    UserWalletGateway,
)


class FakeTenantRegistryGateway(TenantRegistryGateway):
    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._tenants: dict[str, TenantRegistryRecord] = {}
        self._jobs: dict[str, TenantProvisionJobRecord] = {}

    def allocate_tenant_id(self) -> str:
        return self._tenant_id

    def insert_tenant(self, record: TenantRegistryRecord) -> None:
        self._tenants[record.tenant_id] = record

    def update_tenant_status(
        self,
        tenant_id: str,
        status: str,
        updated_at: datetime,
        reason_code: str,
    ) -> None:
        existing = self._tenants[tenant_id]
        self._tenants[tenant_id] = TenantRegistryRecord(
            tenant_id=existing.tenant_id,
            tenant_name=existing.tenant_name,
            channel_type=existing.channel_type,
            status=status,
            route_prefix=existing.route_prefix,
            image_tag=existing.image_tag,
            created_at=existing.created_at,
            updated_at=updated_at,
            reason_code=reason_code,
        )

    def insert_job(self, record: TenantProvisionJobRecord) -> None:
        self._jobs[record.job_id] = record

    def update_job_status(
        self,
        job_id: str,
        status: str,
        updated_at: datetime,
        reason_code: str,
    ) -> None:
        existing = self._jobs[job_id]
        self._jobs[job_id] = TenantProvisionJobRecord(
            job_id=existing.job_id,
            tenant_id=existing.tenant_id,
            status=status,
            created_at=existing.created_at,
            updated_at=updated_at,
            reason_code=reason_code,
        )

    def get_tenant(self, tenant_id: str) -> TenantRegistryRecord:
        return self._tenants[tenant_id]


class FakeTenantRuntimeGateway(TenantRuntimeGateway):
    def __init__(self) -> None:
        self.last_runtime_spec: TenantRuntimeSpec | None = None
        self.started_compose_file: Path | None = None

    def write_tenant_files(self, tenants_root: Path, runtime_spec: TenantRuntimeSpec) -> Path:
        self.last_runtime_spec = runtime_spec
        tenant_dir = tenants_root / runtime_spec.tenant_id
        tenant_dir.mkdir(parents=True, exist_ok=True)
        compose_file = tenant_dir / "compose.yaml"
        compose_file.write_text("services: {}\n", encoding="utf-8")
        return compose_file

    def start_tenant(self, compose_file: Path) -> None:
        self.started_compose_file = compose_file

    def stop_tenant(self, tenants_root: Path, tenant_id: str) -> None:
        _ = tenants_root
        _ = tenant_id

    def remove_tenant_files(self, tenants_root: Path, tenant_id: str) -> None:
        _ = tenants_root
        _ = tenant_id

    def read_tenant_env(self, tenants_root: Path, tenant_id: str) -> dict[str, str]:
        _ = tenants_root
        _ = tenant_id
        return {"OPENAI_API_KEY": "fake-key"}


class FakeTraefikGateway(TraefikGateway):
    def __init__(self) -> None:
        self.last_router_name: str | None = None

    def wait_router_ready(self, router_name: str) -> None:
        self.last_router_name = router_name

    def wait_router_removed(self, router_name: str) -> None:
        _ = router_name


class FakeTenantKeyProvider(TenantKeyProvider):
    def provider_name(self) -> str:
        return "skeleton"

    def issue_openai_api_key(self, tenant_id: str) -> str:
        return f"vk-{tenant_id}"

    def revoke_openai_api_key(self, tenant_id: str, api_key: str) -> None:
        _ = tenant_id
        _ = api_key

    def sync_topup_budget_usd(self, tenant_id: str, api_key: str, budget_delta_usd: str) -> None:
        _ = tenant_id
        _ = api_key
        _ = budget_delta_usd


class FakeLiteLLMTenantKeyProvider(TenantKeyProvider):
    def __init__(self) -> None:
        self.synced_calls: list[dict[str, str]] = []

    def provider_name(self) -> str:
        return "litellm"

    def issue_openai_api_key(self, tenant_id: str) -> str:
        return f"sk-{tenant_id}"

    def revoke_openai_api_key(self, tenant_id: str, api_key: str) -> None:
        _ = tenant_id
        _ = api_key

    def sync_topup_budget_usd(self, tenant_id: str, api_key: str, budget_delta_usd: str) -> None:
        self.synced_calls.append(
            {
                "tenant_id": tenant_id,
                "api_key": api_key,
                "budget_delta_usd": budget_delta_usd,
            }
        )


class FakeUserWalletGateway(UserWalletGateway):
    def __init__(self) -> None:
        self.last_topup_call: dict[str, str] | None = None

    def get_user_balance(self, tenant_id: str, user_id: str) -> UserBalanceRecord:
        return UserBalanceRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            balance="0.000000",
            currency="USD",
            updated_at=datetime.now(tz=timezone.utc),
        )

    def topup(
        self,
        tenant_id: str,
        user_id: str,
        amount: str,
        currency: str,
        source: str,
        source_ref: str,
        request_id: str,
        idempotency_key: str,
        metadata: dict[str, str],
        occurred_at: datetime,
        created_at: datetime,
    ) -> UserLedgerRecord:
        self.last_topup_call = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "amount": amount,
            "currency": currency,
            "idempotency_key": idempotency_key,
        }
        _ = amount
        _ = source
        _ = source_ref
        _ = request_id
        _ = idempotency_key
        _ = metadata
        _ = occurred_at
        return UserLedgerRecord(
            ledger_id=1,
            tenant_id=tenant_id,
            user_id=user_id,
            event_type="topup",
            delta="1.000000",
            balance_after="1.000000",
            currency=currency,
            source="manual",
            source_ref="test",
            request_id="req",
            idempotency_key="idem",
            metadata={},
            occurred_at=created_at,
            created_at=created_at,
            idempotent_replay=False,
        )

    def charge(
        self,
        tenant_id: str,
        user_id: str,
        amount: str,
        currency: str,
        source: str,
        source_ref: str,
        request_id: str,
        idempotency_key: str,
        metadata: dict[str, str],
        occurred_at: datetime,
        created_at: datetime,
    ) -> UserLedgerRecord:
        _ = amount
        _ = source
        _ = source_ref
        _ = request_id
        _ = idempotency_key
        _ = metadata
        _ = occurred_at
        return UserLedgerRecord(
            ledger_id=2,
            tenant_id=tenant_id,
            user_id=user_id,
            event_type="llm_usage",
            delta="-1.000000",
            balance_after="0.000000",
            currency=currency,
            source="manual",
            source_ref="test",
            request_id="req",
            idempotency_key="idem",
            metadata={},
            occurred_at=created_at,
            created_at=created_at,
            idempotent_replay=False,
        )


class TenantProvisionServiceTest(unittest.TestCase):
    def test_provision_generates_backend_fields_and_marks_running(self) -> None:
        requested_at = datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc)
        platform_config = TenantProvisionPlatformConfig(
            image_tag="openclaw/lobster:1.4.2-alpine",
            service_port=3000,
            openai_api_base="https://api.openai.com/v1",
            openai_api_key="platform-openai-key",
            log_level="INFO",
        )
        registry = FakeTenantRegistryGateway(tenant_id="tenant-0001")
        runtime = FakeTenantRuntimeGateway()
        traefik = FakeTraefikGateway()
        key_provider = FakeTenantKeyProvider()
        wallet_gateway = FakeUserWalletGateway()
        with tempfile.TemporaryDirectory() as tmpdir:
            service = TenantProvisionService(
                project_root=Path(tmpdir),
                platform_config=platform_config,
                tenant_registry_gateway=registry,
                tenant_runtime_gateway=runtime,
                traefik_gateway=traefik,
                tenant_key_provider=key_provider,
                user_wallet_gateway=wallet_gateway,
                litellm_topup_fx_rates_to_usd={},
                litellm_topup_sync_state_file=Path(tmpdir) / "litellm-topup-sync-state.json",
                logger=logging.getLogger("test"),
            )
            result = service.provision(
                request=TenantProvisionRequest(
                    tenant_name="Acme Corp",
                    channel_type="feishu",
                    channel_config={
                        "FEISHU_APP_ID": "app-id",
                        "FEISHU_APP_SECRET": "app-secret",
                        "FEISHU_VERIFICATION_TOKEN": "verify-token",
                    },
                    requested_at=requested_at,
                )
            )

        self.assertEqual(result.tenant_id, "tenant-0001")
        self.assertEqual(result.route_prefix, "/tenant/tenant-0001")
        self.assertEqual(result.image_tag, "openclaw/lobster:1.4.2-alpine")
        self.assertEqual(result.status, "RUNNING")
        self.assertEqual(traefik.last_router_name, "tenant-0001")
        self.assertIsNotNone(runtime.last_runtime_spec)
        assert runtime.last_runtime_spec is not None
        self.assertEqual(runtime.last_runtime_spec.openai_api_key, "vk-tenant-0001")
        self.assertEqual(runtime.last_runtime_spec.service_port, 3000)
        self.assertEqual(runtime.last_runtime_spec.route_prefix, "/tenant/tenant-0001")
        tenant_record = registry.get_tenant(tenant_id="tenant-0001")
        self.assertEqual(tenant_record.status, "RUNNING")

    def test_provision_fails_when_required_channel_config_is_missing(self) -> None:
        platform_config = TenantProvisionPlatformConfig(
            image_tag="openclaw/lobster:1.4.2-alpine",
            service_port=3000,
            openai_api_base="https://api.openai.com/v1",
            openai_api_key="platform-openai-key",
            log_level="INFO",
        )
        registry = FakeTenantRegistryGateway(tenant_id="tenant-0001")
        runtime = FakeTenantRuntimeGateway()
        traefik = FakeTraefikGateway()
        key_provider = FakeTenantKeyProvider()
        wallet_gateway = FakeUserWalletGateway()
        with tempfile.TemporaryDirectory() as tmpdir:
            service = TenantProvisionService(
                project_root=Path(tmpdir),
                platform_config=platform_config,
                tenant_registry_gateway=registry,
                tenant_runtime_gateway=runtime,
                traefik_gateway=traefik,
                tenant_key_provider=key_provider,
                user_wallet_gateway=wallet_gateway,
                litellm_topup_fx_rates_to_usd={},
                litellm_topup_sync_state_file=Path(tmpdir) / "litellm-topup-sync-state.json",
                logger=logging.getLogger("test"),
            )
            with self.assertRaises(ClawOSError) as context:
                service.provision(
                    request=TenantProvisionRequest(
                        tenant_name="Acme Corp",
                        channel_type="feishu",
                        channel_config={
                            "FEISHU_APP_ID": "app-id",
                            "FEISHU_APP_SECRET": "app-secret",
                        },
                        requested_at=datetime.now(tz=timezone.utc),
                    )
                )
        self.assertEqual(context.exception.code, ErrorCode.MISSING_ENV_KEY)

    def test_topup_converts_to_usd_and_syncs_litellm_budget(self) -> None:
        requested_at = datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc)
        platform_config = TenantProvisionPlatformConfig(
            image_tag="openclaw/lobster:1.4.2-alpine",
            service_port=3000,
            openai_api_base="https://api.openai.com/v1",
            openai_api_key="platform-openai-key",
            log_level="INFO",
        )
        registry = FakeTenantRegistryGateway(tenant_id="tenant-0001")
        runtime = FakeTenantRuntimeGateway()
        traefik = FakeTraefikGateway()
        key_provider = FakeLiteLLMTenantKeyProvider()
        wallet_gateway = FakeUserWalletGateway()
        with tempfile.TemporaryDirectory() as tmpdir:
            service = TenantProvisionService(
                project_root=Path(tmpdir),
                platform_config=platform_config,
                tenant_registry_gateway=registry,
                tenant_runtime_gateway=runtime,
                traefik_gateway=traefik,
                tenant_key_provider=key_provider,
                user_wallet_gateway=wallet_gateway,
                litellm_topup_fx_rates_to_usd={"USD": "1", "CNY": "0.138"},
                litellm_topup_sync_state_file=Path(tmpdir) / "litellm-topup-sync-state.json",
                logger=logging.getLogger("test"),
            )
            service.provision(
                request=TenantProvisionRequest(
                    tenant_name="Acme Corp",
                    channel_type="feishu",
                    channel_config={
                        "FEISHU_APP_ID": "app-id",
                        "FEISHU_APP_SECRET": "app-secret",
                        "FEISHU_VERIFICATION_TOKEN": "verify-token",
                    },
                    requested_at=requested_at,
                )
            )

            result = service.topup_user_wallet(
                request=UserWalletMutationRequest(
                    tenant_id="tenant-0001",
                    user_id="user-001",
                    amount="100",
                    currency="CNY",
                    source="manual_topup",
                    source_ref="topup-1",
                    request_id="req-topup-1",
                    idempotency_key="idem-topup-1",
                    metadata={},
                    requested_at=requested_at,
                )
            )

        self.assertEqual(result.currency, "USD")
        assert wallet_gateway.last_topup_call is not None
        self.assertEqual(wallet_gateway.last_topup_call["amount"], "13.800000")
        self.assertEqual(wallet_gateway.last_topup_call["currency"], "USD")
        self.assertEqual(len(key_provider.synced_calls), 1)
        self.assertEqual(key_provider.synced_calls[0]["budget_delta_usd"], "13.800000")


if __name__ == "__main__":
    unittest.main()
