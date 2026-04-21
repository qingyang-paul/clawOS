import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
import urllib.error
import urllib.request

from clawos_cli.domain.error_codes import ErrorCode
from clawos_cli.domain.exceptions import ClawOSError


class LiteLLMSpendGateway(ABC):
    @abstractmethod
    def fetch_spend_logs(self) -> list[dict[str, Any]]:
        raise NotImplementedError


class HttpLiteLLMSpendGateway(LiteLLMSpendGateway):
    def __init__(
        self,
        base_url: str,
        master_key: str,
        request_timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._master_key = master_key
        self._request_timeout_seconds = request_timeout_seconds

    def fetch_spend_logs(self) -> list[dict[str, Any]]:
        request = urllib.request.Request(
            url=f"{self._base_url}/spend/logs",
            method="GET",
            headers={"Authorization": f"Bearer {self._master_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._request_timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            response_body = ""
            if exc.fp is not None:
                response_body = exc.fp.read().decode("utf-8", errors="ignore")
            raise ClawOSError(
                code=ErrorCode.HEALTHCHECK_TIMEOUT,
                message=(
                    "failed to fetch litellm spend logs "
                    f"status={exc.code} reason={exc.reason} body={response_body}"
                ),
            ) from exc
        except urllib.error.URLError as exc:
            raise ClawOSError(
                code=ErrorCode.HEALTHCHECK_TIMEOUT,
                message=f"failed to fetch litellm spend logs: {exc}",
            ) from exc

        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ClawOSError(
                code=ErrorCode.HEALTHCHECK_TIMEOUT,
                message=f"litellm /spend/logs returned non-json payload: {payload}",
            ) from exc
        return self._normalize_logs(decoded=decoded)

    def _normalize_logs(self, decoded: Any) -> list[dict[str, Any]]:
        if isinstance(decoded, list):
            return [row for row in decoded if isinstance(row, dict)]
        if isinstance(decoded, dict):
            for field in ("data", "logs", "spend_logs"):
                value = decoded.get(field)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
        return []


def extract_litellm_charge_event(raw: dict[str, Any]) -> dict[str, str] | None:
    metadata = raw.get("metadata")
    metadata_dict: dict[str, Any] = metadata if isinstance(metadata, dict) else {}

    tenant_id_candidates = (
        metadata_dict.get("tenant_id"),
        metadata_dict.get("x-tenant-id"),
        raw.get("key_alias"),
        raw.get("api_key_alias"),
    )
    tenant_id = _pick_first_non_empty_str(tenant_id_candidates)
    if tenant_id is None:
        return None

    user_id_candidates = (
        metadata_dict.get("user_id"),
        raw.get("user_id"),
        raw.get("user"),
    )
    user_id = _pick_first_non_empty_str(user_id_candidates)
    if user_id is None:
        return None

    request_id_candidates = (
        raw.get("request_id"),
        raw.get("id"),
        raw.get("event_id"),
        raw.get("call_id"),
    )
    request_id = _pick_first_non_empty_str(request_id_candidates)
    if request_id is None:
        return None

    amount_candidates = (
        raw.get("spend"),
        raw.get("cost"),
        raw.get("response_cost"),
        raw.get("cost_usd"),
        raw.get("total_cost"),
    )
    amount = _pick_first_decimal(amount_candidates)
    if amount is None:
        return None
    if amount <= Decimal("0"):
        return None

    occurred_at = _extract_occurred_at(raw=raw)
    metadata_for_ledger = {
        "litellm_request_id": request_id,
        "litellm_model": str(raw.get("model", "")),
    }
    for key, value in metadata_dict.items():
        metadata_for_ledger[f"metadata_{key}"] = _stringify_json(value)

    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "request_id": request_id,
        "source_ref": request_id,
        "idempotency_key": f"litellm:{request_id}",
        "amount": format(amount.quantize(Decimal("0.000001")), "f"),
        "currency": "USD",
        "occurred_at": occurred_at.isoformat(),
        "metadata_json": json.dumps(metadata_for_ledger, ensure_ascii=True),
    }


def _pick_first_non_empty_str(values: tuple[object, ...]) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip() != "":
            return value
    return None


def _pick_first_decimal(values: tuple[object, ...]) -> Decimal | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text == "":
            continue
        try:
            return Decimal(text)
        except InvalidOperation:
            continue
    return None


def _extract_occurred_at(raw: dict[str, Any]) -> datetime:
    candidate_fields = ("end_time", "created_at", "timestamp")
    for field in candidate_fields:
        value = raw.get(field)
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text == "":
            continue
        try:
            normalized = text.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue
    return datetime.now(tz=timezone.utc)


def _stringify_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=True)
