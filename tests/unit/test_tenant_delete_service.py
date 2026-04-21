import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from clawos_cli.application.tenant_provision_dto import (
    TenantDeleteRequest,
    TenantProvisionPlatformConfig,
)
from clawos_cli.application.tenant_provision_service import TenantProvisionService
from clawos_cli.infrastructure.tenant_key_provider import TenantKeyProvider
from clawos_cli.infrastructure.tenant_registry_gateway import (
    JsonTenantRegistryGateway,
    TenantRegistryRecord,
)
from clawos_cli.infrastructure.tenant_runtime_gateway import TenantRuntimeGateway, TenantRuntimeSpec
from clawos_cli.infrastructure.traefik_gateway import TraefikGateway
from clawos_cli.infrastructure.user_wallet_gateway import UserWalletGateway


class FakeTenantRuntimeGateway(TenantRuntimeGateway):
    def __init__(self) -> None:
        self.stopped_tenant_id: str | None = None
        self.removed_tenant_id: str | None = None

    def write_tenant_files(self, tenants_root: Path, runtime_spec: TenantRuntimeSpec) -> Path:
        _ = runtime_spec
        return tenants_root / "compose.yaml"

    def start_tenant(self, compose_file: Path) -> None:
        _ = compose_file

    def stop_tenant(self, tenants_root: Path, tenant_id: str) -> None:
        _ = tenants_root
        self.stopped_tenant_id = tenant_id

    def remove_tenant_files(self, tenants_root: Path, tenant_id: str) -> None:
        _ = tenants_root
        self.removed_tenant_id = tenant_id

    def read_tenant_env(self, tenants_root: Path, tenant_id: str) -> dict[str, str]:
        _ = tenants_root
        _ = tenant_id
        return {"OPENAI_API_KEY": "vk-tenant-0001"}


class FakeTraefikGateway(TraefikGateway):
    def __init__(self) -> None:
        self.removed_router_name: str | None = None

    def wait_router_ready(self, router_name: str) -> None:
        _ = router_name

    def wait_router_removed(self, router_name: str) -> None:
        self.removed_router_name = router_name


class FakeTenantKeyProvider(TenantKeyProvider):
    def __init__(self) -> None:
        self.revoked_key: str | None = None

    def provider_name(self) -> str:
        return "skeleton"

    def issue_openai_api_key(self, tenant_id: str) -> str:
        return f"vk-{tenant_id}"

    def revoke_openai_api_key(self, tenant_id: str, api_key: str) -> None:
        _ = tenant_id
        self.revoked_key = api_key

    def sync_topup_budget_usd(self, tenant_id: str, api_key: str, budget_delta_usd: str) -> None:
        _ = tenant_id
        _ = api_key
        _ = budget_delta_usd


class FakeUserWalletGateway(UserWalletGateway):
    def get_user_balance(self, tenant_id: str, user_id: str):
        raise RuntimeError("not used in delete test")

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
    ):
        _ = (
            tenant_id,
            user_id,
            amount,
            currency,
            source,
            source_ref,
            request_id,
            idempotency_key,
            metadata,
            occurred_at,
            created_at,
        )
        raise RuntimeError("not used in delete test")

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
    ):
        _ = (
            tenant_id,
            user_id,
            amount,
            currency,
            source,
            source_ref,
            request_id,
            idempotency_key,
            metadata,
            occurred_at,
            created_at,
        )
        raise RuntimeError("not used in delete test")


class TenantDeleteServiceTest(unittest.TestCase):
    def test_delete_tenant_releases_runtime_and_updates_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            registry_gateway = JsonTenantRegistryGateway(registry_file=project_root / "registry.json")
            registry_gateway.insert_tenant(
                record=TenantRegistryRecord(
                    tenant_id="tenant-0001",
                    tenant_name="Acme",
                    channel_type="feishu",
                    status="RUNNING",
                    route_prefix="/tenant/tenant-0001",
                    image_tag="traefik/whoami:v1.10.2",
                    created_at=datetime(2026, 4, 20, 8, 0, 0, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 4, 20, 8, 0, 0, tzinfo=timezone.utc),
                    reason_code="",
                )
            )

            runtime_gateway = FakeTenantRuntimeGateway()
            traefik_gateway = FakeTraefikGateway()
            key_provider = FakeTenantKeyProvider()
            service = TenantProvisionService(
                project_root=project_root,
                platform_config=TenantProvisionPlatformConfig(
                    image_tag="traefik/whoami:v1.10.2",
                    service_port=80,
                    openai_api_base="http://127.0.0.1:4000/v1",
                    openai_api_key="platform-key",
                    log_level="INFO",
                ),
                tenant_registry_gateway=registry_gateway,
                tenant_runtime_gateway=runtime_gateway,
                traefik_gateway=traefik_gateway,
                tenant_key_provider=key_provider,
                user_wallet_gateway=FakeUserWalletGateway(),
                litellm_topup_fx_rates_to_usd={},
                litellm_topup_sync_state_file=project_root / "litellm-topup-sync-state.json",
                logger=logging.getLogger("test"),
            )

            result = service.delete_tenant(
                request=TenantDeleteRequest(
                    tenant_id="tenant-0001",
                    requested_at=datetime(2026, 4, 20, 8, 1, 0, tzinfo=timezone.utc),
                )
            )

            self.assertEqual(result.status, "DELETED")
            self.assertEqual(runtime_gateway.stopped_tenant_id, "tenant-0001")
            self.assertEqual(runtime_gateway.removed_tenant_id, "tenant-0001")
            self.assertEqual(traefik_gateway.removed_router_name, "tenant-0001")
            self.assertEqual(key_provider.revoked_key, "vk-tenant-0001")

            tenant = registry_gateway.get_tenant(tenant_id="tenant-0001")
            self.assertEqual(tenant.status, "DELETED")


if __name__ == "__main__":
    unittest.main()
