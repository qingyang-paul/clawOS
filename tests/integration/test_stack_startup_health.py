import json
import os
import socket
import subprocess
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTROL_PLANE_ENV_FILE = PROJECT_ROOT / ".tmp" / "control-plane.integration.env"


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


def _fetch_json_with_retry(url: str, timeout_seconds: int, interval_seconds: float) -> dict[str, object]:
    started_at = time.time()
    while time.time() - started_at <= timeout_seconds:
        try:
            with urlopen(url, timeout=3) as response:
                payload = response.read().decode("utf-8")
                parsed = json.loads(payload)
                if isinstance(parsed, dict):
                    return parsed
                raise AssertionError(f"response is not object: url={url} payload={payload}")
        except URLError:
            time.sleep(interval_seconds)
    raise AssertionError(f"http check timeout: {url}")


class StackStartupHealthIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        env = dict(os.environ)
        if not _docker_accessible(env=env):
            raise unittest.SkipTest("docker daemon is not accessible for integration tests")
        _write_control_plane_test_env()

    def test_traefik_starts_and_dashboard_api_available(self) -> None:
        env = dict(os.environ)
        _run_make_target(target="traefik-up", make_vars={}, env=env)

        result = _run_command(
            command=["docker", "inspect", "clawos-traefik", "--format", "{{.State.Running}}"],
            env=env,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "true")

        with urlopen("http://127.0.0.1:8080/api/http/routers", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertIsInstance(payload, list)

    def test_litellm_starts_and_port_is_reachable(self) -> None:
        env = dict(os.environ)
        env["LITELLM_MASTER_KEY"] = "sk-integration-master-key"
        _run_make_target(target="litellm-verify-up", make_vars={}, env=env)

        result = _run_command(
            command=["docker", "inspect", "clawos-litellm-verify", "--format", "{{.State.Running}}"],
            env=env,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "true")

        with socket.create_connection(("127.0.0.1", 4000), timeout=3):
            pass

        try:
            with urlopen("http://127.0.0.1:4000/", timeout=3):
                pass
        except HTTPError as exc:
            self.assertIn(exc.code, (401, 404, 405))

    def test_control_plane_starts_and_healthz_ok(self) -> None:
        env = dict(os.environ)
        _run_make_target(target="traefik-up", make_vars={}, env=env)
        _run_make_target(
            target="control-plane-up",
            make_vars={"CONTROL_PLANE_ENV_FILE": str(CONTROL_PLANE_ENV_FILE)},
            env=env,
        )

        payload = _fetch_json_with_retry(
            url="http://127.0.0.1:8080/control-plane/healthz",
            timeout_seconds=30,
            interval_seconds=1,
        )
        self.assertEqual(payload.get("status"), "ok")


if __name__ == "__main__":
    unittest.main()
