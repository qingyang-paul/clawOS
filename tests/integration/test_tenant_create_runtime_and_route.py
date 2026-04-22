import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTROL_PLANE_ENV_FILE = PROJECT_ROOT / ".tmp" / "control-plane.integration.env"
TENANT_REGISTRY_FILE = PROJECT_ROOT / ".tmp" / "control-plane" / "tenant-registry.json"
USER_WALLET_FILE = PROJECT_ROOT / ".tmp" / "control-plane" / "user-wallet.json"


def _run_command(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def _run_make_target(target: str, make_vars: dict[str, str], env: dict[str, str]) -> str:
    command = ["make", target]
    for key in sorted(make_vars.keys()):
        command.append(f"{key}={make_vars[key]}")
    result = _run_command(command=command, env=env)
    if result.returncode != 0:
        raise AssertionError(
            f"make {target} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


def _docker_accessible(env: dict[str, str]) -> bool:
    result = _run_command(command=["docker", "info"], env=env)
    return result.returncode == 0


def _write_control_plane_test_env() -> None:
    CONTROL_PLANE_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONTROL_PLANE_ENV_FILE.write_text(
        (
            "CONTROL_PLANE_PORT=18090\n"
            "CONTROL_PLANE_API_BASE_URL=http://127.0.0.1:8080/control-plane\n"
            "TRAEFIK_DASHBOARD_API_URL=http://127.0.0.1:8080\n"
            "TRAEFIK_MAX_WAIT_SECONDS=30\n"
            "TRAEFIK_POLL_INTERVAL_SECONDS=1\n"
            "PLATFORM_IMAGE_TAG=traefik/whoami:v1.10.2\n"
            "PLATFORM_SERVICE_PORT=80\n"
            "PLATFORM_OPENAI_API_BASE=https://api.openai.com/v1\n"
            "PLATFORM_OPENAI_API_KEY=test-platform-key\n"
            "PLATFORM_LOG_LEVEL=INFO\n"
            "TENANT_KEY_PROVIDER=skeleton\n"
            "TENANT_KEY_PREFIX=vk_tenant_\n"
            "TENANT_KEY_ENTROPY_BYTES=24\n"
            "LITELLM_AUTO_CHARGE_ENABLED=false\n"
        ),
        encoding="utf-8",
    )


def _extract_json_payload(raw_text: str) -> dict[str, object]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip() != ""]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise AssertionError(f"cannot parse json payload from output:\n{raw_text}")


def _fetch_traefik_routers() -> list[dict[str, object]]:
    with urlopen("http://127.0.0.1:8080/api/http/routers", timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise AssertionError(f"invalid router payload: {payload}")
    return payload


def _wait_route_ok(route_path: str, timeout_seconds: int, interval_seconds: float) -> str:
    started_at = time.time()
    request = Request(url=f"http://127.0.0.1{route_path}", method="GET")
    while time.time() - started_at <= timeout_seconds:
        try:
            with urlopen(request, timeout=3) as response:
                body = response.read().decode("utf-8")
                if response.status == 200:
                    return body
        except HTTPError:
            pass
        time.sleep(interval_seconds)
    raise AssertionError(f"route not healthy in time: {route_path}")


class TenantCreateRuntimeAndRouteIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        env = dict(os.environ)
        if not _docker_accessible(env=env):
            raise unittest.SkipTest("docker daemon is not accessible for integration tests")
        _write_control_plane_test_env()

    def test_create_tenant_creates_registry_runtime_and_route(self) -> None:
        env = dict(os.environ)
        _run_make_target(target="traefik-up", make_vars={}, env=env)
        _run_make_target(
            target="control-plane-up",
            make_vars={"CONTROL_PLANE_ENV_FILE": str(CONTROL_PLANE_ENV_FILE)},
            env=env,
        )

        tenant_name = f"integration-feishu-{int(time.time())}"
        output = _run_make_target(
            target="tenant-create-feishu",
            make_vars={
                "CONTROL_PLANE_ENV_FILE": str(CONTROL_PLANE_ENV_FILE),
                "TENANT_NAME": tenant_name,
                "FEISHU_APP_ID": "integration-app-id",
                "FEISHU_APP_SECRET": "integration-app-secret",
                "FEISHU_VERIFICATION_TOKEN": "integration-verify-token",
            },
            env=env,
        )
        payload = _extract_json_payload(raw_text=output)
        tenant_id = str(payload.get("tenant_id", ""))
        self.assertTrue(tenant_id.startswith("tenant-"))

        try:
            self.assertTrue(TENANT_REGISTRY_FILE.exists())
            self.assertTrue(USER_WALLET_FILE.exists())

            registry = json.loads(TENANT_REGISTRY_FILE.read_text(encoding="utf-8"))
            tenants = registry.get("tenants", [])
            jobs = registry.get("jobs", [])
            self.assertTrue(any(row.get("tenant_id") == tenant_id for row in tenants))
            self.assertTrue(any(row.get("tenant_id") == tenant_id for row in jobs))
            self.assertTrue(any(row.get("tenant_id") == tenant_id and row.get("status") == "RUNNING" for row in tenants))
            self.assertTrue(any(row.get("tenant_id") == tenant_id and row.get("status") == "DONE" for row in jobs))

            wallet = json.loads(USER_WALLET_FILE.read_text(encoding="utf-8"))
            self.assertIn("user_balances", wallet)
            self.assertIn("user_ledger", wallet)

            tenant_compose_file = PROJECT_ROOT / "tenants" / tenant_id / "compose.yaml"
            tenant_env_file = PROJECT_ROOT / "tenants" / tenant_id / ".env"
            self.assertTrue(tenant_compose_file.exists())
            self.assertTrue(tenant_env_file.exists())

            inspect_result = _run_command(
                command=["docker", "inspect", f"clawos-{tenant_id}", "--format", "{{.State.Running}}"],
                env=env,
            )
            self.assertEqual(inspect_result.returncode, 0, msg=inspect_result.stderr)
            self.assertEqual(inspect_result.stdout.strip(), "true")

            routers = _fetch_traefik_routers()
            self.assertTrue(
                any(tenant_id in str(router.get("name", "")) for router in routers),
                msg=f"router for tenant not found: tenant_id={tenant_id}",
            )

            route_body = _wait_route_ok(
                route_path=f"/tenant/{tenant_id}",
                timeout_seconds=30,
                interval_seconds=1,
            )
            self.assertIn("X-Forwarded-Prefix", route_body)
            self.assertIn(f"/tenant/{tenant_id}", route_body)
        finally:
            if tenant_id != "":
                _run_make_target(
                    target="tenant-delete",
                    make_vars={
                        "CONTROL_PLANE_ENV_FILE": str(CONTROL_PLANE_ENV_FILE),
                        "TENANT_ID": tenant_id,
                    },
                    env=env,
                )


if __name__ == "__main__":
    unittest.main()
