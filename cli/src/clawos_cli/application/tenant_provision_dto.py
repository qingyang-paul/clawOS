from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TenantProvisionPlatformConfig:
    image_tag: str
    service_port: int
    openai_api_base: str
    openai_api_key: str
    log_level: str


@dataclass(frozen=True)
class TenantProvisionRequest:
    tenant_name: str
    channel_type: str
    channel_config: dict[str, str]
    requested_at: datetime


@dataclass(frozen=True)
class TenantProvisionResult:
    tenant_id: str
    route_prefix: str
    image_tag: str
    status: str
    job_id: str


@dataclass(frozen=True)
class TenantDeleteRequest:
    tenant_id: str
    requested_at: datetime


@dataclass(frozen=True)
class TenantDeleteResult:
    tenant_id: str
    status: str


@dataclass(frozen=True)
class UserWalletMutationRequest:
    tenant_id: str
    user_id: str
    amount: str
    currency: str
    source: str
    source_ref: str
    request_id: str
    idempotency_key: str
    metadata: dict[str, str]
    requested_at: datetime


@dataclass(frozen=True)
class UserWalletMutationResult:
    tenant_id: str
    user_id: str
    event_type: str
    delta: str
    balance_after: str
    currency: str
    ledger_id: int
    idempotent_replay: bool


@dataclass(frozen=True)
class UserBalanceResult:
    tenant_id: str
    user_id: str
    balance: str
    currency: str
    updated_at: datetime
