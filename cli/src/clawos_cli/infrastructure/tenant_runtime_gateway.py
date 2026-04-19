from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import subprocess

from clawos_cli.domain.error_codes import ErrorCode
from clawos_cli.domain.exceptions import ClawOSError


@dataclass(frozen=True)
class TenantRuntimeSpec:
    tenant_id: str
    tenant_name: str
    image_tag: str
    service_port: int
    route_prefix: str
    openai_api_base: str
    openai_api_key: str
    log_level: str
    channel_type: str
    channel_config: dict[str, str]


class TenantRuntimeGateway(ABC):
    @abstractmethod
    def write_tenant_files(self, tenants_root: Path, runtime_spec: TenantRuntimeSpec) -> Path:
        raise NotImplementedError

    @abstractmethod
    def start_tenant(self, compose_file: Path) -> None:
        raise NotImplementedError


class LocalTenantRuntimeGateway(TenantRuntimeGateway):
    def write_tenant_files(self, tenants_root: Path, runtime_spec: TenantRuntimeSpec) -> Path:
        tenant_directory = tenants_root / runtime_spec.tenant_id
        tenant_directory.mkdir(parents=True, exist_ok=True)
        (tenant_directory / "data").mkdir(parents=True, exist_ok=True)
        compose_file = tenant_directory / "compose.yaml"
        env_file = tenant_directory / ".env"

        env_payload = {
            "TENANT_ID": runtime_spec.tenant_id,
            "TENANT_NAME": runtime_spec.tenant_name,
            "CHANNEL_TYPE": runtime_spec.channel_type,
            "OPENAI_API_BASE": runtime_spec.openai_api_base,
            "OPENAI_API_KEY": runtime_spec.openai_api_key,
            "LOG_LEVEL": runtime_spec.log_level,
        }
        for key, value in runtime_spec.channel_config.items():
            env_payload[key] = value

        env_content = "".join(f"{key}={value}\n" for key, value in sorted(env_payload.items()))
        env_file.write_text(env_content, encoding="utf-8")

        service_name = runtime_spec.tenant_id.replace("-", "_")
        router_name = runtime_spec.tenant_id
        service_name_for_traefik = f"{service_name}-svc"
        middleware_name = f"{service_name}-strip"
        compose_content = (
            "services:\n"
            f"  {service_name}:\n"
            f"    image: {runtime_spec.image_tag}\n"
            f"    container_name: clawos-{runtime_spec.tenant_id}\n"
            "    restart: unless-stopped\n"
            "    env_file:\n"
            "      - ./.env\n"
            "    volumes:\n"
            "      - ./data:/app/data\n"
            "    networks:\n"
            "      - clawos_public\n"
            "    labels:\n"
            "      - traefik.enable=true\n"
            f"      - traefik.http.routers.{router_name}.rule=PathPrefix(`{runtime_spec.route_prefix}`)\n"
            f"      - traefik.http.routers.{router_name}.entrypoints=web\n"
            f"      - traefik.http.routers.{router_name}.service={service_name_for_traefik}\n"
            f"      - traefik.http.routers.{router_name}.middlewares={middleware_name}\n"
            f"      - traefik.http.middlewares.{middleware_name}.stripprefix.prefixes={runtime_spec.route_prefix}\n"
            f"      - traefik.http.services.{service_name_for_traefik}.loadbalancer.server.port={runtime_spec.service_port}\n"
            "\n"
            "networks:\n"
            "  clawos_public:\n"
            "    name: clawos_public\n"
        )
        compose_file.write_text(compose_content, encoding="utf-8")
        return compose_file

    def start_tenant(self, compose_file: Path) -> None:
        command = [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "up",
            "-d",
        ]
        process = subprocess.run(
            command,
            cwd=compose_file.parent,
            text=True,
            capture_output=True,
            check=False,
        )
        if process.returncode == 0:
            return
        raise ClawOSError(
            code=ErrorCode.CONTAINER_START_FAILED,
            message=(
                "failed to start tenant container via docker compose; "
                f"stderr={process.stderr.strip()}"
            ),
        )
