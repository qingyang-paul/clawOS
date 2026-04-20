from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import subprocess
import shutil

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

    @abstractmethod
    def stop_tenant(self, tenants_root: Path, tenant_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def remove_tenant_files(self, tenants_root: Path, tenant_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_tenant_env(self, tenants_root: Path, tenant_id: str) -> dict[str, str]:
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

    def stop_tenant(self, tenants_root: Path, tenant_id: str) -> None:
        compose_file = tenants_root / tenant_id / "compose.yaml"
        if compose_file.exists():
            command = [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "down",
                "--remove-orphans",
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
        fallback_command = [
            "docker",
            "container",
            "rm",
            "-f",
            f"clawos-{tenant_id}",
        ]
        fallback_process = subprocess.run(
            fallback_command,
            text=True,
            capture_output=True,
            check=False,
        )
        if fallback_process.returncode == 0:
            return
        stderr = fallback_process.stderr.strip()
        if "No such container" in stderr:
            return
        raise ClawOSError(
            code=ErrorCode.CONTAINER_STOP_FAILED,
            message=(
                "failed to stop tenant container; "
                f"stderr={stderr}"
            ),
        )

    def remove_tenant_files(self, tenants_root: Path, tenant_id: str) -> None:
        tenant_directory = tenants_root / tenant_id
        if tenant_directory.exists():
            shutil.rmtree(tenant_directory)

    def read_tenant_env(self, tenants_root: Path, tenant_id: str) -> dict[str, str]:
        env_file = tenants_root / tenant_id / ".env"
        if not env_file.exists():
            return {}
        entries: dict[str, str] = {}
        for line in env_file.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if raw == "" or raw.startswith("#"):
                continue
            if "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            entries[key] = value
        return entries
