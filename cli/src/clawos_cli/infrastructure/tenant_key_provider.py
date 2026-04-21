from abc import ABC, abstractmethod
import json
import secrets
from decimal import Decimal, InvalidOperation
import urllib.parse
import urllib.error
import urllib.request

from clawos_cli.domain.error_codes import ErrorCode
from clawos_cli.domain.exceptions import ClawOSError


class TenantKeyProvider(ABC):
    @abstractmethod
    def provider_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def issue_openai_api_key(self, tenant_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def revoke_openai_api_key(self, tenant_id: str, api_key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def sync_topup_budget_usd(self, tenant_id: str, api_key: str, budget_delta_usd: str) -> None:
        raise NotImplementedError


class SkeletonTenantKeyProvider(TenantKeyProvider):
    def __init__(self, key_prefix: str, entropy_bytes: int) -> None:
        self._key_prefix = key_prefix
        self._entropy_bytes = entropy_bytes

    def provider_name(self) -> str:
        return "skeleton"

    def issue_openai_api_key(self, tenant_id: str) -> str:
        suffix = secrets.token_urlsafe(self._entropy_bytes)
        return f"{self._key_prefix}{tenant_id}-{suffix}"

    def revoke_openai_api_key(self, tenant_id: str, api_key: str) -> None:
        _ = tenant_id
        _ = api_key
        # Skeleton provider keeps keys local-only and does not maintain a remote registry.

    def sync_topup_budget_usd(self, tenant_id: str, api_key: str, budget_delta_usd: str) -> None:
        _ = tenant_id
        _ = api_key
        _ = budget_delta_usd


class LiteLLMTenantKeyProvider(TenantKeyProvider):
    def __init__(
        self,
        base_url: str,
        master_key: str,
        model_name: str,
        request_timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._master_key = master_key
        self._model_name = model_name
        self._request_timeout_seconds = request_timeout_seconds

    def provider_name(self) -> str:
        return "litellm"

    def issue_openai_api_key(self, tenant_id: str) -> str:
        payload = self._call_json(
            method="POST",
            endpoint="/key/generate",
            body={
                "models": [self._model_name],
                "key_alias": tenant_id,
                "metadata": {"tenant_id": tenant_id},
            },
            error_code=ErrorCode.LITELLM_KEY_ISSUE_FAILED,
        )
        if not isinstance(payload, dict):
            raise ClawOSError(
                code=ErrorCode.LITELLM_KEY_ISSUE_FAILED,
                message=f"invalid /key/generate response type: {type(payload)}",
            )
        for key_name in ("key", "token", "api_key"):
            value = payload.get(key_name)
            if isinstance(value, str) and value.strip() != "":
                return value
        raise ClawOSError(
            code=ErrorCode.LITELLM_KEY_ISSUE_FAILED,
            message=f"/key/generate response missing key field: {payload}",
        )

    def revoke_openai_api_key(self, tenant_id: str, api_key: str) -> None:
        if api_key.strip() == "":
            return
        _ = tenant_id
        self._call_json(
            method="POST",
            endpoint="/key/delete",
            body={"keys": [api_key]},
            error_code=ErrorCode.LITELLM_KEY_REVOKE_FAILED,
        )

    def sync_topup_budget_usd(self, tenant_id: str, api_key: str, budget_delta_usd: str) -> None:
        _ = tenant_id
        try:
            budget_delta = Decimal(budget_delta_usd)
        except InvalidOperation as exc:
            raise ClawOSError(
                code=ErrorCode.LITELLM_BUDGET_SYNC_FAILED,
                message=f"invalid budget_delta_usd={budget_delta_usd}",
            ) from exc
        if budget_delta <= Decimal("0"):
            raise ClawOSError(
                code=ErrorCode.LITELLM_BUDGET_SYNC_FAILED,
                message=f"budget_delta_usd must be positive, got={budget_delta_usd}",
            )

        key_info = self._get_key_info(api_key=api_key)
        current_max_budget_raw = key_info.get("max_budget")
        current_max_budget = Decimal("0")
        if current_max_budget_raw is not None and str(current_max_budget_raw).strip() != "":
            try:
                current_max_budget = Decimal(str(current_max_budget_raw))
            except InvalidOperation as exc:
                raise ClawOSError(
                    code=ErrorCode.LITELLM_BUDGET_SYNC_FAILED,
                    message=f"invalid current max_budget from litellm: {current_max_budget_raw}",
                ) from exc

        new_max_budget = current_max_budget + budget_delta
        normalized_budget = format(new_max_budget.quantize(Decimal("0.000001")), "f")
        payload_options = (
            {"key": api_key, "max_budget": normalized_budget},
            {"keys": [api_key], "max_budget": normalized_budget},
        )
        last_error: ClawOSError | None = None
        for body in payload_options:
            try:
                self._call_json(
                    method="POST",
                    endpoint="/key/update",
                    body=body,
                    error_code=ErrorCode.LITELLM_BUDGET_SYNC_FAILED,
                )
                return
            except ClawOSError as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise ClawOSError(
            code=ErrorCode.LITELLM_BUDGET_SYNC_FAILED,
            message="failed to sync litellm budget",
        )

    def _call_json(
        self,
        method: str,
        endpoint: str,
        body: dict[str, object] | None,
        error_code: ErrorCode,
    ) -> dict[str, object] | list[object]:
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self._base_url}{endpoint}",
            method=method,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._master_key}",
            },
            data=data,
        )
        try:
            with urllib.request.urlopen(request, timeout=self._request_timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            response_body = ""
            if exc.fp is not None:
                response_body = exc.fp.read().decode("utf-8", errors="ignore")
            raise ClawOSError(
                code=error_code,
                message=(
                    f"litellm endpoint failed method={method} endpoint={endpoint} "
                    f"status={exc.code} reason={exc.reason} body={response_body}"
                ),
            ) from exc
        except urllib.error.URLError as exc:
            raise ClawOSError(
                code=error_code,
                message=f"failed to call litellm endpoint method={method} endpoint={endpoint}: {exc}",
            ) from exc
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ClawOSError(
                code=error_code,
                message=f"litellm endpoint returned non-json method={method} endpoint={endpoint}",
            ) from exc
        if isinstance(parsed, dict) or isinstance(parsed, list):
            return parsed
        raise ClawOSError(
            code=error_code,
            message=f"litellm endpoint returned invalid payload type method={method} endpoint={endpoint}",
        )

    def _get_key_info(self, api_key: str) -> dict[str, object]:
        query = urllib.parse.urlencode({"key": api_key})
        payload = self._call_json(
            method="GET",
            endpoint=f"/key/info?{query}",
            body=None,
            error_code=ErrorCode.LITELLM_BUDGET_SYNC_FAILED,
        )
        if not isinstance(payload, dict):
            raise ClawOSError(
                code=ErrorCode.LITELLM_BUDGET_SYNC_FAILED,
                message=f"/key/info returned invalid payload type: {type(payload)}",
            )
        info = payload.get("info")
        if isinstance(info, dict):
            return {str(key): value for key, value in info.items()}
        return {str(key): value for key, value in payload.items()}
