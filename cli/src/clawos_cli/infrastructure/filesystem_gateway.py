import json
import shutil
import tarfile
from abc import ABC, abstractmethod
from pathlib import Path

from clawos_cli.domain.error_codes import ErrorCode
from clawos_cli.domain.exceptions import ClawOSError


class FilesystemGateway(ABC):
    @abstractmethod
    def ensure_directory(self, directory: Path) -> None:
        raise NotImplementedError

    @abstractmethod
    def copy_tenant_data(self, tenants_root: Path, tenants_staging_directory: Path) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def write_manifest(
        self,
        manifest_file: Path,
        backup_id: str,
        created_at: str,
        included_tenants: list[str],
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def create_archive(self, source_directory: Path, archive_file: Path) -> None:
        raise NotImplementedError

    @abstractmethod
    def remove_directory(self, directory: Path) -> None:
        raise NotImplementedError

    @abstractmethod
    def extract_archive(self, archive_file: Path, target_directory: Path) -> None:
        raise NotImplementedError

    @abstractmethod
    def find_single_backup_directory(self, extracted_root: Path) -> Path:
        raise NotImplementedError

    @abstractmethod
    def read_manifest(self, manifest_file: Path) -> dict[str, object]:
        raise NotImplementedError


class LocalFilesystemGateway(FilesystemGateway):
    def ensure_directory(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)

    def copy_tenant_data(self, tenants_root: Path, tenants_staging_directory: Path) -> list[str]:
        included_tenants: list[str] = []
        self.ensure_directory(tenants_staging_directory)
        if not tenants_root.exists():
            return included_tenants

        for tenant_directory in sorted(tenants_root.iterdir()):
            tenant_data_directory = tenant_directory / "data"
            if not tenant_directory.is_dir() or not tenant_data_directory.exists():
                continue
            destination_directory = tenants_staging_directory / tenant_directory.name / "data"
            destination_directory.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(tenant_data_directory, destination_directory)
            included_tenants.append(tenant_directory.name)
        return included_tenants

    def write_manifest(
        self,
        manifest_file: Path,
        backup_id: str,
        created_at: str,
        included_tenants: list[str],
    ) -> None:
        payload = {
            "backup_id": backup_id,
            "created_at": created_at,
            "included_tenants": included_tenants,
            "schema_version": "v1",
            "mode": "skeleton",
        }
        manifest_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def create_archive(self, source_directory: Path, archive_file: Path) -> None:
        with tarfile.open(archive_file, "w:gz") as tar:
            tar.add(source_directory, arcname=source_directory.name)

    def remove_directory(self, directory: Path) -> None:
        if directory.exists():
            shutil.rmtree(directory)

    def extract_archive(self, archive_file: Path, target_directory: Path) -> None:
        with tarfile.open(archive_file, "r:gz") as tar:
            tar.extractall(target_directory)

    def find_single_backup_directory(self, extracted_root: Path) -> Path:
        child_directories = [path for path in extracted_root.iterdir() if path.is_dir()]
        if len(child_directories) != 1:
            raise ClawOSError(
                code=ErrorCode.BACKUP_FORMAT_INVALID,
                message="backup archive must contain exactly one root directory",
            )
        return child_directories[0]

    def read_manifest(self, manifest_file: Path) -> dict[str, object]:
        if not manifest_file.exists():
            raise ClawOSError(
                code=ErrorCode.BACKUP_FORMAT_INVALID,
                message="manifest.json is missing in backup archive",
            )
        try:
            return json.loads(manifest_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ClawOSError(
                code=ErrorCode.BACKUP_FORMAT_INVALID,
                message=f"manifest.json is invalid json: {exc}",
            ) from exc

