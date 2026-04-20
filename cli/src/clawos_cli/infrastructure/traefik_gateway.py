import json
import time
from abc import ABC, abstractmethod
from urllib.error import URLError
from urllib.request import urlopen

from clawos_cli.domain.error_codes import ErrorCode
from clawos_cli.domain.exceptions import ClawOSError


class TraefikGateway(ABC):
    @abstractmethod
    def wait_router_ready(self, router_name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def wait_router_removed(self, router_name: str) -> None:
        raise NotImplementedError


class HttpTraefikGateway(TraefikGateway):
    def __init__(self, dashboard_api_url: str, max_wait_seconds: float, poll_interval_seconds: float) -> None:
        self._dashboard_api_url = dashboard_api_url.rstrip("/")
        self._max_wait_seconds = max_wait_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def wait_router_ready(self, router_name: str) -> None:
        started_at = time.time()
        while time.time() - started_at <= self._max_wait_seconds:
            routers = self._fetch_routers()
            if self._is_router_present(routers=routers, router_name=router_name):
                return
            time.sleep(self._poll_interval_seconds)

        raise ClawOSError(
            code=ErrorCode.TRAEFIK_ROUTER_NOT_READY,
            message=f"traefik router is not ready in time: {router_name}",
        )

    def wait_router_removed(self, router_name: str) -> None:
        started_at = time.time()
        while time.time() - started_at <= self._max_wait_seconds:
            routers = self._fetch_routers()
            if not self._is_router_present(routers=routers, router_name=router_name):
                return
            time.sleep(self._poll_interval_seconds)
        raise ClawOSError(
            code=ErrorCode.TRAEFIK_ROUTER_NOT_REMOVED,
            message=f"traefik router is still active after timeout: {router_name}",
        )

    def _fetch_routers(self) -> list[dict[str, object]]:
        endpoint = f"{self._dashboard_api_url}/api/http/routers"
        try:
            with urlopen(endpoint, timeout=3) as response:
                payload = response.read().decode("utf-8")
        except URLError as exc:
            raise ClawOSError(
                code=ErrorCode.HEALTHCHECK_TIMEOUT,
                message=f"failed to query traefik dashboard api: {exc}",
            ) from exc
        raw = json.loads(payload)
        if not isinstance(raw, list):
            raise ClawOSError(
                code=ErrorCode.HEALTHCHECK_TIMEOUT,
                message="invalid traefik dashboard response: expected list",
            )
        return raw

    def _is_router_present(self, routers: list[dict[str, object]], router_name: str) -> bool:
        for router in routers:
            name = str(router.get("name", ""))
            if name in (f"{router_name}@docker", router_name):
                return True
        return False
