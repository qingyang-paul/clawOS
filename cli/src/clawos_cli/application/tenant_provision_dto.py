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
