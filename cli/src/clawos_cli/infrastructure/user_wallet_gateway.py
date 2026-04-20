import json
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from clawos_cli.domain.error_codes import ErrorCode
from clawos_cli.domain.exceptions import ClawOSError


@dataclass(frozen=True)
class UserBalanceRecord:
    tenant_id: str
    user_id: str
    balance: str
    currency: str
    updated_at: datetime


@dataclass(frozen=True)
class UserLedgerRecord:
    ledger_id: int
    tenant_id: str
    user_id: str
    event_type: str
    delta: str
    balance_after: str
    currency: str
    source: str
    source_ref: str
    request_id: str
    idempotency_key: str
    metadata: dict[str, str]
    occurred_at: datetime
    created_at: datetime
    idempotent_replay: bool


class UserWalletGateway(ABC):
    @abstractmethod
    def get_user_balance(self, tenant_id: str, user_id: str) -> UserBalanceRecord:
        raise NotImplementedError

    @abstractmethod
    def topup(
        self,
        tenant_id: str,
        user_id: str,
        amount: str,
        currency: str,
        source: str,
        source_ref: str,
        request_id: str,
        idempotency_key: str,
        metadata: dict[str, str],
        occurred_at: datetime,
        created_at: datetime,
    ) -> UserLedgerRecord:
        raise NotImplementedError

    @abstractmethod
    def charge(
        self,
        tenant_id: str,
        user_id: str,
        amount: str,
        currency: str,
        source: str,
        source_ref: str,
        request_id: str,
        idempotency_key: str,
        metadata: dict[str, str],
        occurred_at: datetime,
        created_at: datetime,
    ) -> UserLedgerRecord:
        raise NotImplementedError


class JsonUserWalletGateway(UserWalletGateway):
    def __init__(self, wallet_file: Path) -> None:
        self._wallet_file = wallet_file
        self._wallet_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self._wallet_file.exists():
            self._write(
                payload={
                    "last_ledger_id": 0,
                    "user_balances": [],
                    "user_ledger": [],
                }
            )

    def get_user_balance(self, tenant_id: str, user_id: str) -> UserBalanceRecord:
        with self._lock:
            payload = self._read()
            for record in payload["user_balances"]:
                if record["tenant_id"] != tenant_id or record["user_id"] != user_id:
                    continue
                return UserBalanceRecord(
                    tenant_id=record["tenant_id"],
                    user_id=record["user_id"],
                    balance=record["balance"],
                    currency=record["currency"],
                    updated_at=datetime.fromisoformat(record["updated_at"]),
                )
        raise ClawOSError(
            code=ErrorCode.USER_BALANCE_NOT_FOUND,
            message=f"user balance not found tenant_id={tenant_id} user_id={user_id}",
        )

    def topup(
        self,
        tenant_id: str,
        user_id: str,
        amount: str,
        currency: str,
        source: str,
        source_ref: str,
        request_id: str,
        idempotency_key: str,
        metadata: dict[str, str],
        occurred_at: datetime,
        created_at: datetime,
    ) -> UserLedgerRecord:
        return self._mutate_balance(
            tenant_id=tenant_id,
            user_id=user_id,
            amount=amount,
            currency=currency,
            source=source,
            source_ref=source_ref,
            request_id=request_id,
            idempotency_key=idempotency_key,
            metadata=metadata,
            occurred_at=occurred_at,
            created_at=created_at,
            event_type="topup",
            is_charge=False,
        )

    def charge(
        self,
        tenant_id: str,
        user_id: str,
        amount: str,
        currency: str,
        source: str,
        source_ref: str,
        request_id: str,
        idempotency_key: str,
        metadata: dict[str, str],
        occurred_at: datetime,
        created_at: datetime,
    ) -> UserLedgerRecord:
        return self._mutate_balance(
            tenant_id=tenant_id,
            user_id=user_id,
            amount=amount,
            currency=currency,
            source=source,
            source_ref=source_ref,
            request_id=request_id,
            idempotency_key=idempotency_key,
            metadata=metadata,
            occurred_at=occurred_at,
            created_at=created_at,
            event_type="llm_usage",
            is_charge=True,
        )

    def _mutate_balance(
        self,
        tenant_id: str,
        user_id: str,
        amount: str,
        currency: str,
        source: str,
        source_ref: str,
        request_id: str,
        idempotency_key: str,
        metadata: dict[str, str],
        occurred_at: datetime,
        created_at: datetime,
        event_type: str,
        is_charge: bool,
    ) -> UserLedgerRecord:
        parsed_amount = self._parse_amount(amount=amount)
        if parsed_amount <= Decimal("0"):
            raise ClawOSError(
                code=ErrorCode.WALLET_REQUEST_INVALID,
                message=f"amount must be positive: {amount}",
            )
        if currency.strip() == "":
            raise ClawOSError(
                code=ErrorCode.WALLET_REQUEST_INVALID,
                message="currency is required",
            )
        if idempotency_key.strip() == "":
            raise ClawOSError(
                code=ErrorCode.WALLET_REQUEST_INVALID,
                message="idempotency_key is required",
            )
        if source.strip() == "" or source_ref.strip() == "":
            raise ClawOSError(
                code=ErrorCode.WALLET_REQUEST_INVALID,
                message="source and source_ref are required",
            )

        with self._lock:
            payload = self._read()
            existing_ledger = self._find_by_idempotency(
                payload=payload,
                tenant_id=tenant_id,
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
            if existing_ledger is not None:
                return self._to_ledger_record(raw=existing_ledger, idempotent_replay=True)

            balance_raw = self._find_user_balance(payload=payload, tenant_id=tenant_id, user_id=user_id)
            if balance_raw is None:
                if is_charge:
                    raise ClawOSError(
                        code=ErrorCode.LLM_BUDGET_EXHAUSTED,
                        message=f"insufficient balance for tenant_id={tenant_id} user_id={user_id}",
                    )
                balance_raw = {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "balance": "0",
                    "currency": currency,
                    "updated_at": created_at.isoformat(),
                }
                payload["user_balances"].append(balance_raw)

            if balance_raw["currency"] != currency:
                raise ClawOSError(
                    code=ErrorCode.WALLET_REQUEST_INVALID,
                    message=(
                        "currency mismatch for user balance: "
                        f"tenant_id={tenant_id} user_id={user_id} expected={balance_raw['currency']} got={currency}"
                    ),
                )

            current_balance = self._parse_amount(amount=balance_raw["balance"])
            if is_charge and current_balance < parsed_amount:
                raise ClawOSError(
                    code=ErrorCode.LLM_BUDGET_EXHAUSTED,
                    message=(
                        "insufficient balance for llm_usage charge: "
                        f"tenant_id={tenant_id} user_id={user_id} balance={current_balance} amount={parsed_amount}"
                    ),
                )

            delta = -parsed_amount if is_charge else parsed_amount
            balance_after = current_balance + delta
            serialized_delta = self._serialize_amount(amount=delta)
            serialized_balance_after = self._serialize_amount(amount=balance_after)

            payload["last_ledger_id"] = int(payload["last_ledger_id"]) + 1
            ledger_row = {
                "ledger_id": payload["last_ledger_id"],
                "tenant_id": tenant_id,
                "user_id": user_id,
                "event_type": event_type,
                "delta": serialized_delta,
                "balance_after": serialized_balance_after,
                "currency": currency,
                "source": source,
                "source_ref": source_ref,
                "request_id": request_id,
                "idempotency_key": idempotency_key,
                "metadata": metadata,
                "occurred_at": occurred_at.isoformat(),
                "created_at": created_at.isoformat(),
            }
            payload["user_ledger"].append(ledger_row)
            balance_raw["balance"] = serialized_balance_after
            balance_raw["updated_at"] = created_at.isoformat()
            self._write(payload=payload)
            return self._to_ledger_record(raw=ledger_row, idempotent_replay=False)

    def _find_user_balance(
        self,
        payload: dict[str, object],
        tenant_id: str,
        user_id: str,
    ) -> dict[str, object] | None:
        for record in payload["user_balances"]:
            if record["tenant_id"] == tenant_id and record["user_id"] == user_id:
                return record
        return None

    def _find_by_idempotency(
        self,
        payload: dict[str, object],
        tenant_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> dict[str, object] | None:
        for record in payload["user_ledger"]:
            if (
                record["tenant_id"] == tenant_id
                and record["user_id"] == user_id
                and record["idempotency_key"] == idempotency_key
            ):
                return record
        return None

    def _to_ledger_record(self, raw: dict[str, object], idempotent_replay: bool) -> UserLedgerRecord:
        return UserLedgerRecord(
            ledger_id=int(raw["ledger_id"]),
            tenant_id=str(raw["tenant_id"]),
            user_id=str(raw["user_id"]),
            event_type=str(raw["event_type"]),
            delta=str(raw["delta"]),
            balance_after=str(raw["balance_after"]),
            currency=str(raw["currency"]),
            source=str(raw["source"]),
            source_ref=str(raw["source_ref"]),
            request_id=str(raw["request_id"]),
            idempotency_key=str(raw["idempotency_key"]),
            metadata={str(key): str(value) for key, value in dict(raw["metadata"]).items()},
            occurred_at=datetime.fromisoformat(str(raw["occurred_at"])),
            created_at=datetime.fromisoformat(str(raw["created_at"])),
            idempotent_replay=idempotent_replay,
        )

    def _parse_amount(self, amount: str) -> Decimal:
        try:
            return Decimal(amount)
        except InvalidOperation as exc:
            raise ClawOSError(
                code=ErrorCode.WALLET_REQUEST_INVALID,
                message=f"invalid amount: {amount}",
            ) from exc

    def _serialize_amount(self, amount: Decimal) -> str:
        normalized = amount.quantize(Decimal("0.000001"))
        return format(normalized, "f")

    def _read(self) -> dict[str, object]:
        return json.loads(self._wallet_file.read_text(encoding="utf-8"))

    def _write(self, payload: dict[str, object]) -> None:
        self._wallet_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
