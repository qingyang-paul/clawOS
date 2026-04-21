import json
import logging
import tempfile
import unittest
from pathlib import Path
from typing import Any

from clawos_cli.application.litellm_auto_charge_worker import LiteLLMAutoChargeConfig, LiteLLMAutoChargeWorker
from clawos_cli.application.tenant_provision_dto import UserWalletMutationResult
from clawos_cli.infrastructure.litellm_spend_gateway import LiteLLMSpendGateway, extract_litellm_charge_event


class FakeLiteLLMSpendGateway(LiteLLMSpendGateway):
    def __init__(self, logs: list[dict[str, Any]]) -> None:
        self._logs = logs

    def fetch_spend_logs(self) -> list[dict[str, Any]]:
        return self._logs


class FakeTenantProvisionService:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def charge_user_wallet(self, request):
        self.calls.append(
            {
                "tenant_id": request.tenant_id,
                "user_id": request.user_id,
                "amount": request.amount,
                "request_id": request.request_id,
                "idempotency_key": request.idempotency_key,
            }
        )
        return UserWalletMutationResult(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            event_type="llm_usage",
            delta=f"-{request.amount}",
            balance_after="8.750000",
            currency=request.currency,
            ledger_id=1,
            idempotent_replay=False,
        )


class LiteLLMAutoChargeWorkerTest(unittest.TestCase):
    def test_extract_litellm_charge_event(self) -> None:
        event = extract_litellm_charge_event(
            raw={
                "request_id": "chatcmpl-1",
                "spend": "0.1234",
                "model": "gpt-4o-mini",
                "metadata": {"tenant_id": "tenant-0001", "user_id": "user-001"},
                "created_at": "2026-04-21T01:00:00Z",
            }
        )
        assert event is not None
        self.assertEqual(event["tenant_id"], "tenant-0001")
        self.assertEqual(event["user_id"], "user-001")
        self.assertEqual(event["idempotency_key"], "litellm:chatcmpl-1")
        self.assertEqual(event["amount"], "0.123400")

    def test_sync_once_applies_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            service = FakeTenantProvisionService()
            worker = LiteLLMAutoChargeWorker(
                spend_gateway=FakeLiteLLMSpendGateway(
                    logs=[
                        {
                            "request_id": "chatcmpl-1",
                            "spend": "0.250000",
                            "metadata": {"tenant_id": "tenant-0001", "user_id": "user-001"},
                            "created_at": "2026-04-21T01:00:00Z",
                        }
                    ]
                ),
                tenant_provision_service=service,  # type: ignore[arg-type]
                config=LiteLLMAutoChargeConfig(
                    poll_interval_seconds=2,
                    state_file=state_file,
                ),
                logger=logging.getLogger("test"),
            )
            worker.sync_once()
            worker.sync_once()
            self.assertEqual(len(service.calls), 1)
            self.assertEqual(service.calls[0]["tenant_id"], "tenant-0001")
            self.assertEqual(service.calls[0]["request_id"], "chatcmpl-1")
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertIn("litellm:chatcmpl-1", state["processed_idempotency_keys"])


if __name__ == "__main__":
    unittest.main()
