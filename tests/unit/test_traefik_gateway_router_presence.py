import unittest

from clawos_cli.infrastructure.traefik_gateway import HttpTraefikGateway


class TraefikGatewayRouterPresenceTest(unittest.TestCase):
    def test_router_present_with_docker_suffix(self) -> None:
        gateway = HttpTraefikGateway(
            dashboard_api_url="http://127.0.0.1:8080",
            max_wait_seconds=1,
            poll_interval_seconds=0.1,
        )
        routers = [{"name": "tenant-0001@docker"}]
        self.assertTrue(gateway._is_router_present(routers=routers, router_name="tenant-0001"))

    def test_router_present_with_plain_name(self) -> None:
        gateway = HttpTraefikGateway(
            dashboard_api_url="http://127.0.0.1:8080",
            max_wait_seconds=1,
            poll_interval_seconds=0.1,
        )
        routers = [{"name": "tenant-0002"}]
        self.assertTrue(gateway._is_router_present(routers=routers, router_name="tenant-0002"))

    def test_router_not_present(self) -> None:
        gateway = HttpTraefikGateway(
            dashboard_api_url="http://127.0.0.1:8080",
            max_wait_seconds=1,
            poll_interval_seconds=0.1,
        )
        routers = [{"name": "tenant-0003@docker"}]
        self.assertFalse(gateway._is_router_present(routers=routers, router_name="tenant-0004"))


if __name__ == "__main__":
    unittest.main()
