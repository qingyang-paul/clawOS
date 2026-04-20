import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from clawos_cli.domain.error_codes import ErrorCode
from clawos_cli.domain.exceptions import ClawOSError
from clawos_cli.infrastructure.user_wallet_gateway import JsonUserWalletGateway


class UserWalletGatewayTest(unittest.TestCase):
    def test_topup_and_charge_with_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            gateway = JsonUserWalletGateway(wallet_file=Path(tmpdir) / "wallet.json")
            now = datetime(2026, 4, 20, 8, 0, 0, tzinfo=timezone.utc)

            topup = gateway.topup(
                tenant_id="tenant-0001",
                user_id="user-1",
                amount="10",
                currency="USD",
                source="manual_topup",
                source_ref="topup-1",
                request_id="req-topup-1",
                idempotency_key="idem-topup-1",
                metadata={"operator": "unit-test"},
                occurred_at=now,
                created_at=now,
            )
            self.assertEqual(topup.delta, "10.000000")
            self.assertEqual(topup.balance_after, "10.000000")
            self.assertFalse(topup.idempotent_replay)

            replay = gateway.topup(
                tenant_id="tenant-0001",
                user_id="user-1",
                amount="10",
                currency="USD",
                source="manual_topup",
                source_ref="topup-1",
                request_id="req-topup-1",
                idempotency_key="idem-topup-1",
                metadata={"operator": "unit-test"},
                occurred_at=now,
                created_at=now,
            )
            self.assertTrue(replay.idempotent_replay)
            self.assertEqual(replay.ledger_id, topup.ledger_id)

            charge = gateway.charge(
                tenant_id="tenant-0001",
                user_id="user-1",
                amount="3.5",
                currency="USD",
                source="litellm_proxy",
                source_ref="chatcmpl-1",
                request_id="chatcmpl-1",
                idempotency_key="idem-charge-1",
                metadata={"model": "gpt-4o-mini"},
                occurred_at=now,
                created_at=now,
            )
            self.assertEqual(charge.delta, "-3.500000")
            self.assertEqual(charge.balance_after, "6.500000")

            balance = gateway.get_user_balance(tenant_id="tenant-0001", user_id="user-1")
            self.assertEqual(balance.balance, "6.500000")

    def test_charge_fails_when_balance_is_insufficient(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            gateway = JsonUserWalletGateway(wallet_file=Path(tmpdir) / "wallet.json")
            with self.assertRaises(ClawOSError) as context:
                gateway.charge(
                    tenant_id="tenant-0001",
                    user_id="user-2",
                    amount="1",
                    currency="USD",
                    source="litellm_proxy",
                    source_ref="chatcmpl-2",
                    request_id="chatcmpl-2",
                    idempotency_key="idem-charge-2",
                    metadata={"model": "gpt-4o-mini"},
                    occurred_at=datetime(2026, 4, 20, 8, 0, 0, tzinfo=timezone.utc),
                    created_at=datetime(2026, 4, 20, 8, 0, 0, tzinfo=timezone.utc),
                )
        self.assertEqual(context.exception.code, ErrorCode.LLM_BUDGET_EXHAUSTED)


if __name__ == "__main__":
    unittest.main()
