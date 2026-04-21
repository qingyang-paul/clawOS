import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from clawos_cli.application.tenant_provision_dto import UserWalletMutationRequest
from clawos_cli.application.tenant_provision_service import TenantProvisionService
from clawos_cli.domain.error_codes import ErrorCode
from clawos_cli.domain.exceptions import ClawOSError
from clawos_cli.infrastructure.litellm_spend_gateway import LiteLLMSpendGateway, extract_litellm_charge_event


@dataclass(frozen=True)
class LiteLLMAutoChargeConfig:
    poll_interval_seconds: float
    state_file: Path


class LiteLLMAutoChargeWorker:
    def __init__(
        self,
        spend_gateway: LiteLLMSpendGateway,
        tenant_provision_service: TenantProvisionService,
        config: LiteLLMAutoChargeConfig,
        logger: logging.Logger,
    ) -> None:
        self._spend_gateway = spend_gateway
        self._tenant_provision_service = tenant_provision_service
        self._config = config
        self._logger = logger
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._config.state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._config.state_file.exists():
            self._write_state(payload={"processed_idempotency_keys": []})

    def start(self) -> None:
        thread = threading.Thread(target=self._run, name="litellm-auto-charge-worker", daemon=True)
        self._thread = thread
        thread.start()
        self._logger.info(
            "litellm_auto_charge_worker_started poll_interval_seconds=%s state_file=%s",
            self._config.poll_interval_seconds,
            self._config.state_file,
        )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.sync_once()
            except Exception as exc:  # noqa: BLE001
                self._logger.error("litellm_auto_charge_worker_sync_failed error=%s", exc)
            self._stop_event.wait(self._config.poll_interval_seconds)

    def sync_once(self) -> None:
        logs = self._spend_gateway.fetch_spend_logs()
        if len(logs) == 0:
            return
        state = self._read_state()
        processed = set(state["processed_idempotency_keys"])
        updated = False

        for raw in logs:
            event = extract_litellm_charge_event(raw=raw)
            if event is None:
                continue
            idempotency_key = event["idempotency_key"]
            if idempotency_key in processed:
                continue

            metadata = json.loads(event["metadata_json"])
            occurred_at = datetime.fromisoformat(event["occurred_at"])
            try:
                result = self._tenant_provision_service.charge_user_wallet(
                    request=UserWalletMutationRequest(
                        tenant_id=event["tenant_id"],
                        user_id=event["user_id"],
                        amount=event["amount"],
                        currency=event["currency"],
                        source="litellm_pull",
                        source_ref=event["source_ref"],
                        request_id=event["request_id"],
                        idempotency_key=idempotency_key,
                        metadata={str(key): str(value) for key, value in metadata.items()},
                        requested_at=occurred_at,
                    )
                )
                self._logger.info(
                    "litellm_auto_charge_applied tenant_id=%s user_id=%s request_id=%s delta=%s balance_after=%s",
                    result.tenant_id,
                    result.user_id,
                    event["request_id"],
                    result.delta,
                    result.balance_after,
                )
                processed.add(idempotency_key)
                updated = True
            except ClawOSError as exc:
                if exc.code == ErrorCode.LLM_BUDGET_EXHAUSTED:
                    self._logger.warning(
                        "litellm_auto_charge_budget_exhausted tenant_id=%s user_id=%s request_id=%s",
                        event["tenant_id"],
                        event["user_id"],
                        event["request_id"],
                    )
                    continue
                if exc.code in (ErrorCode.TENANT_NOT_FOUND, ErrorCode.TENANT_STATUS_INVALID):
                    self._logger.warning(
                        "litellm_auto_charge_tenant_unavailable tenant_id=%s user_id=%s request_id=%s code=%s",
                        event["tenant_id"],
                        event["user_id"],
                        event["request_id"],
                        exc.code.value,
                    )
                    processed.add(idempotency_key)
                    updated = True
                    continue
                raise

        if updated:
            state["processed_idempotency_keys"] = sorted(processed)
            self._write_state(payload=state)

    def _read_state(self) -> dict[str, list[str]]:
        with self._state_lock:
            raw = json.loads(self._config.state_file.read_text(encoding="utf-8"))
        keys = raw.get("processed_idempotency_keys")
        if isinstance(keys, list):
            return {"processed_idempotency_keys": [str(item) for item in keys]}
        return {"processed_idempotency_keys": []}

    def _write_state(self, payload: dict[str, list[str]]) -> None:
        with self._state_lock:
            self._config.state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
