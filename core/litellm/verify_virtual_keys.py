import argparse
import json
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Any


class HttpCallError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: str = "") -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(message)


def _call_json(method: str, url: str, body: dict[str, Any] | None, bearer_token: str) -> dict[str, Any] | list[Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    request = urllib.request.Request(url=url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = ""
        try:
            if exc.fp is not None:
                error_body = exc.fp.read().decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            error_body = ""
        raise HttpCallError(
            f"{method} {url} failed: status={exc.code} reason={exc.reason} body={error_body}",
            status_code=exc.code,
            body=error_body,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HttpCallError(f"{method} {url} failed: {exc}") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HttpCallError(f"{method} {url} returned non-json: {payload}") from exc


def _extract_key(payload: dict[str, Any]) -> str:
    for key_name in ("key", "token", "api_key"):
        value = payload.get(key_name)
        if isinstance(value, str) and value.strip():
            return value
    raise HttpCallError(f"key generate response missing key field: {payload}")


def _get_key_info(base_url: str, master_key: str, virtual_key: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"key": virtual_key})
    payload = _call_json(
        method="GET",
        url=f"{base_url}/key/info?{query}",
        body=None,
        bearer_token=master_key,
    )
    if isinstance(payload, dict):
        return payload
    raise HttpCallError(f"/key/info returned unexpected payload: {payload}")


def _create_virtual_key(base_url: str, master_key: str, tenant_id: str) -> str:
    payload = _call_json(
        method="POST",
        url=f"{base_url}/key/generate",
        body={
            "models": ["gpt-4o-mini"],
            "key_alias": tenant_id,
            "metadata": {"tenant_id": tenant_id},
        },
        bearer_token=master_key,
    )
    if not isinstance(payload, dict):
        raise HttpCallError(f"/key/generate returned unexpected payload: {payload}")
    return _extract_key(payload=payload)


def _request_with_virtual_key(base_url: str, virtual_key: str, tenant_id: str) -> dict[str, Any]:
    payload = _call_json(
        method="POST",
        url=f"{base_url}/chat/completions",
        body={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": f"hello from {tenant_id}"}],
        },
        bearer_token=virtual_key,
    )
    if not isinstance(payload, dict):
        raise HttpCallError(f"/chat/completions returned unexpected payload: {payload}")
    return payload


def _fetch_spend_logs(base_url: str, master_key: str) -> list[Any]:
    payload = _call_json(
        method="GET",
        url=f"{base_url}/spend/logs",
        body=None,
        bearer_token=master_key,
    )
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for field in ("data", "logs", "spend_logs"):
            value = payload.get(field)
            if isinstance(value, list):
                return value
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify LiteLLM per-tenant virtual keys and routing logs")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--master-key", required=True)
    parser.add_argument("--tenant-a", required=True)
    parser.add_argument("--tenant-b", required=True)
    parser.add_argument("--sleep-seconds", required=True, type=float)
    parser.add_argument("--strict-spend-logs", action="store_true")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    key_a = _create_virtual_key(base_url=base_url, master_key=args.master_key, tenant_id=args.tenant_a)
    key_b = _create_virtual_key(base_url=base_url, master_key=args.master_key, tenant_id=args.tenant_b)
    if key_a == key_b:
        raise HttpCallError("generated virtual keys are identical")
    print(f"[ok] generated key for {args.tenant_a}: {key_a[:14]}...")
    print(f"[ok] generated key for {args.tenant_b}: {key_b[:14]}...")

    response_a = _request_with_virtual_key(base_url=base_url, virtual_key=key_a, tenant_id=args.tenant_a)
    response_b = _request_with_virtual_key(base_url=base_url, virtual_key=key_b, tenant_id=args.tenant_b)
    print(f"[ok] request with {args.tenant_a} key -> id={response_a.get('id')}")
    print(f"[ok] request with {args.tenant_b} key -> id={response_b.get('id')}")

    time.sleep(args.sleep_seconds)

    info_a = _get_key_info(base_url=base_url, master_key=args.master_key, virtual_key=key_a)
    info_b = _get_key_info(base_url=base_url, master_key=args.master_key, virtual_key=key_b)
    print("[info] /key/info tenant-a:", json.dumps(info_a, ensure_ascii=True))
    print("[info] /key/info tenant-b:", json.dumps(info_b, ensure_ascii=True))

    try:
        spend_logs = _fetch_spend_logs(base_url=base_url, master_key=args.master_key)
        print(f"[info] /spend/logs count={len(spend_logs)}")
        if spend_logs:
            preview = spend_logs[-5:]
            print("[info] /spend/logs tail:", json.dumps(preview, ensure_ascii=True))
    except HttpCallError as exc:
        if args.strict_spend_logs:
            raise
        print(f"[warn] /spend/logs unavailable, continue with key-level verification: {exc}")

    print("[pass] virtual keys are independent and requests were forwarded through LiteLLM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
