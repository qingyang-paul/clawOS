import logging

from clawos_cli.application.dto import RestoreRequest, RestoreResult
from clawos_cli.domain.error_codes import ErrorCode
from clawos_cli.domain.exceptions import ClawOSError
from clawos_cli.infrastructure.filesystem_gateway import FilesystemGateway
from clawos_cli.infrastructure.postgres_gateway import PostgresGateway


class RestoreService:
    def __init__(
        self,
        filesystem_gateway: FilesystemGateway,
        postgres_gateway: PostgresGateway,
        logger: logging.Logger,
    ) -> None:
        self._filesystem_gateway = filesystem_gateway
        self._postgres_gateway = postgres_gateway
        self._logger = logger

    def restore(self, request: RestoreRequest) -> RestoreResult:
        if not request.backup_file.exists():
            raise ClawOSError(
                code=ErrorCode.BACKUP_FILE_NOT_FOUND,
                message=f"backup file does not exist: {request.backup_file}",
            )

        restore_id = request.requested_at.strftime("restore-%Y%m%dT%H%M%SZ")
        staging_directory = request.scratch_root / restore_id
        self._filesystem_gateway.ensure_directory(request.scratch_root)
        self._filesystem_gateway.ensure_directory(staging_directory)
        self._logger.info("tenant_restore_start restore_id=%s backup_file=%s", restore_id, request.backup_file)

        self._filesystem_gateway.extract_archive(
            archive_file=request.backup_file,
            target_directory=staging_directory,
        )
        extracted_backup_directory = self._filesystem_gateway.find_single_backup_directory(
            extracted_root=staging_directory
        )
        manifest = self._filesystem_gateway.read_manifest(manifest_file=extracted_backup_directory / "manifest.json")
        backup_id = manifest.get("backup_id")
        included_tenants_raw = manifest.get("included_tenants")
        postgres_dump_file = extracted_backup_directory / "postgres.sql"

        if not isinstance(backup_id, str):
            raise ClawOSError(
                code=ErrorCode.BACKUP_FORMAT_INVALID,
                message="backup_id is missing in manifest",
            )
        if not isinstance(included_tenants_raw, list):
            raise ClawOSError(
                code=ErrorCode.BACKUP_FORMAT_INVALID,
                message="included_tenants is missing in manifest",
            )
        if not postgres_dump_file.exists():
            raise ClawOSError(
                code=ErrorCode.BACKUP_FORMAT_INVALID,
                message="postgres.sql is missing in backup archive",
            )

        # v1 skeleton: DB restore flow is wired via gateway, implementation can be replaced later.
        self._postgres_gateway.restore(project_root=request.project_root, input_file=postgres_dump_file)

        self._filesystem_gateway.remove_directory(staging_directory)
        self._logger.info("tenant_restore_done restore_id=%s backup_id=%s", restore_id, backup_id)
        return RestoreResult(
            backup_id=backup_id,
            included_tenants=tuple(str(tenant_id) for tenant_id in included_tenants_raw),
        )

