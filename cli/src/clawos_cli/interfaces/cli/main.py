import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal, InvalidOperation

from clawos_cli.application.backup_service import BackupService
from clawos_cli.application.dto import BackupRequest, RestoreRequest
from clawos_cli.application.litellm_auto_charge_worker import LiteLLMAutoChargeConfig, LiteLLMAutoChargeWorker
from clawos_cli.application.restore_service import RestoreService
from clawos_cli.application.tenant_provision_dto import TenantProvisionPlatformConfig
from clawos_cli.application.tenant_provision_service import TenantProvisionService
from clawos_cli.domain.error_codes import ErrorCode
from clawos_cli.domain.exceptions import ClawOSError
from clawos_cli.infrastructure.filesystem_gateway import LocalFilesystemGateway
from clawos_cli.infrastructure.litellm_spend_gateway import HttpLiteLLMSpendGateway
from clawos_cli.infrastructure.postgres_gateway import SkeletonPostgresGateway
from clawos_cli.infrastructure.tenant_key_provider import LiteLLMTenantKeyProvider, SkeletonTenantKeyProvider
from clawos_cli.infrastructure.tenant_registry_gateway import JsonTenantRegistryGateway
from clawos_cli.infrastructure.tenant_runtime_gateway import LocalTenantRuntimeGateway
from clawos_cli.infrastructure.traefik_gateway import HttpTraefikGateway
from clawos_cli.infrastructure.user_wallet_gateway import JsonUserWalletGateway
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
    control_plane_serve_parser.add_argument("--wallet-file", required=True)
    control_plane_serve_parser.add_argument("--traefik-dashboard-api-url", required=True)
    control_plane_serve_parser.add_argument("--traefik-max-wait-seconds", required=True, type=float)
    control_plane_serve_parser.add_argument("--traefik-poll-interval-seconds", required=True, type=float)
    control_plane_serve_parser.add_argument("--platform-image-tag", required=True)
    control_plane_serve_parser.add_argument("--platform-service-port", required=True, type=int)
    control_plane_serve_parser.add_argument("--platform-openai-api-base", required=True)
    control_plane_serve_parser.add_argument("--platform-openai-api-key", required=True)
    control_plane_serve_parser.add_argument("--platform-log-level", required=True)
    control_plane_serve_parser.add_argument("--tenant-key-provider", required=True, choices=("skeleton", "litellm"))
    control_plane_serve_parser.add_argument("--tenant-key-prefix")
    control_plane_serve_parser.add_argument("--tenant-key-entropy-bytes", type=int)
    control_plane_serve_parser.add_argument("--litellm-base-url")
    control_plane_serve_parser.add_argument("--litellm-master-key")
    control_plane_serve_parser.add_argument("--litellm-model-name")
    control_plane_serve_parser.add_argument("--litellm-timeout-seconds", type=float)
    control_plane_serve_parser.add_argument("--litellm-topup-fx-rates")
    control_plane_serve_parser.add_argument("--litellm-topup-sync-state-file")
    control_plane_serve_parser.add_argument("--litellm-auto-charge-enabled", required=True, choices=("true", "false"))
    control_plane_serve_parser.add_argument("--litellm-auto-charge-poll-interval-seconds", type=float)
    control_plane_serve_parser.add_argument("--litellm-auto-charge-state-file")
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
    user_wallet_gateway = JsonUserWalletGateway(wallet_file=Path(args.wallet_file))
    tenant_runtime_gateway = LocalTenantRuntimeGateway()
    traefik_gateway = HttpTraefikGateway(
        dashboard_api_url=args.traefik_dashboard_api_url,
        max_wait_seconds=args.traefik_max_wait_seconds,
        poll_interval_seconds=args.traefik_poll_interval_seconds,
    )
    if args.tenant_key_provider == "skeleton":
        if args.tenant_key_prefix is None or args.tenant_key_entropy_bytes is None:
            raise ClawOSError(
                code=ErrorCode.PLATFORM_CONFIG_MISSING,
                message="tenant-key-prefix and tenant-key-entropy-bytes are required for skeleton provider",
            )
        tenant_key_provider = SkeletonTenantKeyProvider(
            key_prefix=args.tenant_key_prefix,
            entropy_bytes=args.tenant_key_entropy_bytes,
        )
    else:
        if args.litellm_base_url is None or args.litellm_master_key is None:
            raise ClawOSError(
                code=ErrorCode.PLATFORM_CONFIG_MISSING,
                message="litellm-base-url and litellm-master-key are required for litellm provider",
            )
        if args.litellm_model_name is None or args.litellm_timeout_seconds is None:
            raise ClawOSError(
                code=ErrorCode.PLATFORM_CONFIG_MISSING,
                message="litellm-model-name and litellm-timeout-seconds are required for litellm provider",
            )
        if args.litellm_topup_fx_rates is None or args.litellm_topup_sync_state_file is None:
            raise ClawOSError(
                code=ErrorCode.PLATFORM_CONFIG_MISSING,
                message="litellm-topup-fx-rates and litellm-topup-sync-state-file are required for litellm provider",
            )
        tenant_key_provider = LiteLLMTenantKeyProvider(
            base_url=args.litellm_base_url,
            master_key=args.litellm_master_key,
            model_name=args.litellm_model_name,
            request_timeout_seconds=args.litellm_timeout_seconds,
        )
    topup_fx_rates = _parse_topup_fx_rates(raw=args.litellm_topup_fx_rates)
    topup_sync_state_file = Path(
        args.litellm_topup_sync_state_file
        if args.litellm_topup_sync_state_file is not None
        else project_root / ".tmp" / "control-plane" / "litellm-topup-sync-state.json"
    )
    tenant_provision_service = TenantProvisionService(
        project_root=project_root,
        platform_config=platform_config,
        tenant_registry_gateway=tenant_registry_gateway,
        tenant_runtime_gateway=tenant_runtime_gateway,
        traefik_gateway=traefik_gateway,
        tenant_key_provider=tenant_key_provider,
        user_wallet_gateway=user_wallet_gateway,
        litellm_topup_fx_rates_to_usd=topup_fx_rates,
        litellm_topup_sync_state_file=topup_sync_state_file,
        logger=logger,
    )
    auto_charge_enabled = args.litellm_auto_charge_enabled == "true"
    if auto_charge_enabled:
        if args.litellm_base_url is None or args.litellm_master_key is None:
            raise ClawOSError(
                code=ErrorCode.PLATFORM_CONFIG_MISSING,
                message="litellm-base-url and litellm-master-key are required when auto charge is enabled",
            )
        if args.litellm_timeout_seconds is None:
            raise ClawOSError(
                code=ErrorCode.PLATFORM_CONFIG_MISSING,
                message="litellm-timeout-seconds is required when auto charge is enabled",
            )
        if (
            args.litellm_auto_charge_poll_interval_seconds is None
            or args.litellm_auto_charge_state_file is None
        ):
            raise ClawOSError(
                code=ErrorCode.PLATFORM_CONFIG_MISSING,
                message=(
                    "litellm-auto-charge-poll-interval-seconds and "
                    "litellm-auto-charge-state-file are required when auto charge is enabled"
                ),
            )
        auto_charge_worker = LiteLLMAutoChargeWorker(
            spend_gateway=HttpLiteLLMSpendGateway(
                base_url=args.litellm_base_url,
                master_key=args.litellm_master_key,
                request_timeout_seconds=args.litellm_timeout_seconds,
            ),
            tenant_provision_service=tenant_provision_service,
            config=LiteLLMAutoChargeConfig(
                poll_interval_seconds=args.litellm_auto_charge_poll_interval_seconds,
                state_file=Path(args.litellm_auto_charge_state_file),
            ),
            logger=logger,
        )
        auto_charge_worker.start()
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


def _parse_topup_fx_rates(raw: str | None) -> dict[str, str]:
    if raw is None:
        return {}
    parsed: dict[str, str] = {}
    for entry in raw.split(","):
        candidate = entry.strip()
        if candidate == "":
            continue
        if ":" not in candidate:
            raise ClawOSError(
                code=ErrorCode.PLATFORM_CONFIG_MISSING,
                message=f"invalid litellm-topup-fx-rates item (expect CURRENCY:RATE): {candidate}",
            )
        currency, rate = candidate.split(":", 1)
        currency_normalized = currency.strip().upper()
        rate_normalized = rate.strip()
        if currency_normalized == "" or rate_normalized == "":
            raise ClawOSError(
                code=ErrorCode.PLATFORM_CONFIG_MISSING,
                message=f"invalid litellm-topup-fx-rates item (empty field): {candidate}",
            )
        try:
            rate_decimal = Decimal(rate_normalized)
        except InvalidOperation as exc:
            raise ClawOSError(
                code=ErrorCode.PLATFORM_CONFIG_MISSING,
                message=f"invalid fx rate for currency={currency_normalized}: {rate_normalized}",
            ) from exc
        if rate_decimal <= Decimal("0"):
            raise ClawOSError(
                code=ErrorCode.PLATFORM_CONFIG_MISSING,
                message=f"fx rate must be positive for currency={currency_normalized}: {rate_normalized}",
            )
        parsed[currency_normalized] = format(rate_decimal, "f")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
