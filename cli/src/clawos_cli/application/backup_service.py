import logging
from pathlib import Path

from clawos_cli.application.dto import BackupRequest, BackupResult
from clawos_cli.infrastructure.filesystem_gateway import FilesystemGateway
from clawos_cli.infrastructure.postgres_gateway import PostgresGateway


class BackupService:
    def __init__(
        self,
        filesystem_gateway: FilesystemGateway,
        postgres_gateway: PostgresGateway,
        logger: logging.Logger,
    ) -> None:
        self._filesystem_gateway = filesystem_gateway
        self._postgres_gateway = postgres_gateway
        self._logger = logger

    def backup(self, request: BackupRequest) -> BackupResult:
        backup_id = request.requested_at.strftime("backup-%Y%m%dT%H%M%SZ")
        staging_directory = request.backup_root / backup_id
        archive_file = request.backup_root / f"{backup_id}.tar.gz"
        manifest_file = staging_directory / "manifest.json"
        postgres_dump_file = staging_directory / "postgres.sql"
        tenants_staging_directory = staging_directory / "tenants"
        tenants_root = request.project_root / "tenants"

        self._logger.info("tenant_backup_start backup_id=%s", backup_id)
        self._filesystem_gateway.ensure_directory(request.backup_root)
        self._filesystem_gateway.ensure_directory(staging_directory)

        # v1 skeleton: DB dump flow is wired via gateway, implementation can be replaced later.
        self._postgres_gateway.backup(project_root=request.project_root, output_file=postgres_dump_file)
        included_tenants = self._filesystem_gateway.copy_tenant_data(
            tenants_root=tenants_root,
            tenants_staging_directory=tenants_staging_directory,
        )
        self._filesystem_gateway.write_manifest(
            manifest_file=manifest_file,
            backup_id=backup_id,
            created_at=request.requested_at.isoformat(),
            included_tenants=included_tenants,
        )
        self._filesystem_gateway.create_archive(
            source_directory=staging_directory,
            archive_file=archive_file,
        )
        self._filesystem_gateway.remove_directory(staging_directory)

        self._logger.info("tenant_backup_done backup_id=%s archive_file=%s", backup_id, archive_file)
        return BackupResult(
            backup_file=archive_file,
            included_tenants=tuple(included_tenants),
        )

