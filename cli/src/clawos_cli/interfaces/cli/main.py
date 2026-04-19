import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from clawos_cli.application.backup_service import BackupService
from clawos_cli.application.dto import BackupRequest, RestoreRequest
from clawos_cli.application.restore_service import RestoreService
from clawos_cli.application.tenant_provision_dto import TenantProvisionPlatformConfig
from clawos_cli.application.tenant_provision_service import TenantProvisionService
from clawos_cli.domain.exceptions import ClawOSError
from clawos_cli.infrastructure.filesystem_gateway import LocalFilesystemGateway
from clawos_cli.infrastructure.postgres_gateway import SkeletonPostgresGateway
from clawos_cli.infrastructure.tenant_key_provider import SkeletonTenantKeyProvider
from clawos_cli.infrastructure.tenant_registry_gateway import JsonTenantRegistryGateway
from clawos_cli.infrastructure.tenant_runtime_gateway import LocalTenantRuntimeGateway
from clawos_cli.infrastructure.traefik_gateway import HttpTraefikGateway
from clawos_cli.interfaces.http.tenant_control_plane import serve_tenant_control_plane


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clawos")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("backup")
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--file", required=True)

    control_plane_parser = subparsers.add_parser("control-plane")
    control_plane_subparsers = control_plane_parser.add_subparsers(dest="control_plane_command", required=True)
    control_plane_serve_parser = control_plane_subparsers.add_parser("serve")
    control_plane_serve_parser.add_argument("--host", required=True)
    control_plane_serve_parser.add_argument("--port", required=True, type=int)
    control_plane_serve_parser.add_argument("--project-root", required=True)
    control_plane_serve_parser.add_argument("--registry-file", required=True)
    control_plane_serve_parser.add_argument("--traefik-dashboard-api-url", required=True)
    control_plane_serve_parser.add_argument("--traefik-max-wait-seconds", required=True, type=float)
    control_plane_serve_parser.add_argument("--traefik-poll-interval-seconds", required=True, type=float)
    control_plane_serve_parser.add_argument("--platform-image-tag", required=True)
    control_plane_serve_parser.add_argument("--platform-service-port", required=True, type=int)
    control_plane_serve_parser.add_argument("--platform-openai-api-base", required=True)
    control_plane_serve_parser.add_argument("--platform-openai-api-key", required=True)
    control_plane_serve_parser.add_argument("--platform-log-level", required=True)
    control_plane_serve_parser.add_argument("--tenant-key-prefix", required=True)
    control_plane_serve_parser.add_argument("--tenant-key-entropy-bytes", required=True, type=int)
    return parser


def configure_logger() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return logging.getLogger("clawos_cli")


def handle_backup(project_root: Path, logger: logging.Logger) -> int:
    filesystem_gateway = LocalFilesystemGateway()
    postgres_gateway = SkeletonPostgresGateway()
    backup_service = BackupService(
        filesystem_gateway=filesystem_gateway,
        postgres_gateway=postgres_gateway,
        logger=logger,
    )
    request = BackupRequest(
        project_root=project_root,
        backup_root=project_root / ".tmp" / "backups",
        requested_at=datetime.now(tz=timezone.utc),
    )
    result = backup_service.backup(request=request)
    print(f"clawos info: backup created at {result.backup_file}")
    return 0


def handle_restore(project_root: Path, backup_file: Path, logger: logging.Logger) -> int:
    filesystem_gateway = LocalFilesystemGateway()
    postgres_gateway = SkeletonPostgresGateway()
    restore_service = RestoreService(
        filesystem_gateway=filesystem_gateway,
        postgres_gateway=postgres_gateway,
        logger=logger,
    )
    request = RestoreRequest(
        project_root=project_root,
        backup_file=backup_file,
        scratch_root=project_root / ".tmp" / "restore",
        requested_at=datetime.now(tz=timezone.utc),
    )
    result = restore_service.restore(request=request)
    print(f"clawos info: restore skeleton validated backup {result.backup_id}")
    return 0


def handle_control_plane_serve(args: argparse.Namespace, logger: logging.Logger) -> int:
    project_root = Path(args.project_root)
    platform_config = TenantProvisionPlatformConfig(
        image_tag=args.platform_image_tag,
        service_port=args.platform_service_port,
        openai_api_base=args.platform_openai_api_base,
        openai_api_key=args.platform_openai_api_key,
        log_level=args.platform_log_level,
    )
    tenant_registry_gateway = JsonTenantRegistryGateway(registry_file=Path(args.registry_file))
    tenant_runtime_gateway = LocalTenantRuntimeGateway()
    traefik_gateway = HttpTraefikGateway(
        dashboard_api_url=args.traefik_dashboard_api_url,
        max_wait_seconds=args.traefik_max_wait_seconds,
        poll_interval_seconds=args.traefik_poll_interval_seconds,
    )
    tenant_key_provider = SkeletonTenantKeyProvider(
        key_prefix=args.tenant_key_prefix,
        entropy_bytes=args.tenant_key_entropy_bytes,
    )
    tenant_provision_service = TenantProvisionService(
        project_root=project_root,
        platform_config=platform_config,
        tenant_registry_gateway=tenant_registry_gateway,
        tenant_runtime_gateway=tenant_runtime_gateway,
        traefik_gateway=traefik_gateway,
        tenant_key_provider=tenant_key_provider,
        logger=logger,
    )
    serve_tenant_control_plane(
        host=args.host,
        port=args.port,
        tenant_provision_service=tenant_provision_service,
        logger=logger,
    )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:])
    logger = configure_logger()
    project_root = Path.cwd()
    try:
        if args.command == "backup":
            return handle_backup(project_root=project_root, logger=logger)
        if args.command == "restore":
            return handle_restore(project_root=project_root, backup_file=Path(args.file), logger=logger)
        if args.command == "control-plane" and args.control_plane_command == "serve":
            return handle_control_plane_serve(args=args, logger=logger)
        parser.print_help()
        return 2
    except ClawOSError as exc:
        print(exc.to_user_message(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
