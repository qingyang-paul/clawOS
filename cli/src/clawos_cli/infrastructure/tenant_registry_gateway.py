import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from clawos_cli.domain.error_codes import ErrorCode
from clawos_cli.domain.exceptions import ClawOSError


@dataclass(frozen=True)
class TenantRegistryRecord:
    tenant_id: str
    tenant_name: str
    channel_type: str
    status: str
    route_prefix: str
    image_tag: str
    created_at: datetime
    updated_at: datetime
    reason_code: str


@dataclass(frozen=True)
class TenantProvisionJobRecord:
    job_id: str
    tenant_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    reason_code: str


class TenantRegistryGateway(ABC):
    @abstractmethod
    def allocate_tenant_id(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def insert_tenant(self, record: TenantRegistryRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def update_tenant_status(
        self,
        tenant_id: str,
        status: str,
        updated_at: datetime,
        reason_code: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def insert_job(self, record: TenantProvisionJobRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def update_job_status(
        self,
        job_id: str,
        status: str,
        updated_at: datetime,
        reason_code: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_tenant(self, tenant_id: str) -> TenantRegistryRecord:
        raise NotImplementedError


class JsonTenantRegistryGateway(TenantRegistryGateway):
    def __init__(self, registry_file: Path) -> None:
        self._registry_file = registry_file
        self._registry_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._registry_file.exists():
            self._write(
                payload={
                    "last_tenant_sequence": 0,
                    "tenants": [],
                    "jobs": [],
                }
            )

    def allocate_tenant_id(self) -> str:
        payload = self._read()
        sequence = int(payload["last_tenant_sequence"]) + 1
        payload["last_tenant_sequence"] = sequence
        self._write(payload=payload)
        return f"tenant-{sequence:04d}"

    def insert_tenant(self, record: TenantRegistryRecord) -> None:
        payload = self._read()
        tenants = payload["tenants"]
        existing = [tenant for tenant in tenants if tenant["tenant_id"] == record.tenant_id]
        if existing:
            raise ClawOSError(
                code=ErrorCode.TENANT_CREATE_REQUEST_INVALID,
                message=f"tenant_id already exists: {record.tenant_id}",
            )
        tenants.append(self._serialize_tenant(record=record))
        self._write(payload=payload)

    def update_tenant_status(
        self,
        tenant_id: str,
        status: str,
        updated_at: datetime,
        reason_code: str,
    ) -> None:
        payload = self._read()
        for tenant in payload["tenants"]:
            if tenant["tenant_id"] != tenant_id:
                continue
            tenant["status"] = status
            tenant["updated_at"] = updated_at.isoformat()
            tenant["reason_code"] = reason_code
            self._write(payload=payload)
            return
        raise ClawOSError(code=ErrorCode.TENANT_NOT_FOUND, message=f"tenant_id not found: {tenant_id}")

    def insert_job(self, record: TenantProvisionJobRecord) -> None:
        payload = self._read()
        payload["jobs"].append(self._serialize_job(record=record))
        self._write(payload=payload)

    def update_job_status(
        self,
        job_id: str,
        status: str,
        updated_at: datetime,
        reason_code: str,
    ) -> None:
        payload = self._read()
        for job in payload["jobs"]:
            if job["job_id"] != job_id:
                continue
            job["status"] = status
            job["updated_at"] = updated_at.isoformat()
            job["reason_code"] = reason_code
            self._write(payload=payload)
            return
        raise ClawOSError(
            code=ErrorCode.CONFIG_FILE_NOT_FOUND,
            message=f"tenant provision job not found: {job_id}",
        )

    def get_tenant(self, tenant_id: str) -> TenantRegistryRecord:
        payload = self._read()
        for tenant in payload["tenants"]:
            if tenant["tenant_id"] != tenant_id:
                continue
            return TenantRegistryRecord(
                tenant_id=tenant["tenant_id"],
                tenant_name=tenant["tenant_name"],
                channel_type=tenant["channel_type"],
                status=tenant["status"],
                route_prefix=tenant["route_prefix"],
                image_tag=tenant["image_tag"],
                created_at=datetime.fromisoformat(tenant["created_at"]),
                updated_at=datetime.fromisoformat(tenant["updated_at"]),
                reason_code=tenant["reason_code"],
            )
        raise ClawOSError(code=ErrorCode.TENANT_NOT_FOUND, message=f"tenant_id not found: {tenant_id}")

    def _serialize_tenant(self, record: TenantRegistryRecord) -> dict[str, str]:
        payload = asdict(record)
        payload["created_at"] = record.created_at.isoformat()
        payload["updated_at"] = record.updated_at.isoformat()
        return payload

    def _serialize_job(self, record: TenantProvisionJobRecord) -> dict[str, str]:
        payload = asdict(record)
        payload["created_at"] = record.created_at.isoformat()
        payload["updated_at"] = record.updated_at.isoformat()
        return payload

    def _read(self) -> dict[str, object]:
        return json.loads(self._registry_file.read_text(encoding="utf-8"))

    def _write(self, payload: dict[str, object]) -> None:
        self._registry_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
