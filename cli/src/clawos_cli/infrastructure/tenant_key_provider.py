from abc import ABC, abstractmethod
import secrets


class TenantKeyProvider(ABC):
    @abstractmethod
    def issue_openai_api_key(self, tenant_id: str) -> str:
        raise NotImplementedError


class SkeletonTenantKeyProvider(TenantKeyProvider):
    def __init__(self, key_prefix: str, entropy_bytes: int) -> None:
        self._key_prefix = key_prefix
        self._entropy_bytes = entropy_bytes

    def issue_openai_api_key(self, tenant_id: str) -> str:
        suffix = secrets.token_urlsafe(self._entropy_bytes)
        return f"{self._key_prefix}{tenant_id}-{suffix}"
