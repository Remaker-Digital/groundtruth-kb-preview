"""Explicit loopback HTTP transport. No configuration, storage, redirect or retry."""
from __future__ import annotations

import time
from http.client import HTTPMessage
from typing import IO, Any, ContextManager, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .codec import canonical_json_bytes, parse_json_bytes
from .endpoint import validate_authority_url
from .errors import AuthorityClientError
from .policy import RequestPolicy, RequestPolicyProvider, _capture_policy
from .types import WireJSON


class RequestOpener(Protocol):
    def open(self, request: Request, *, timeout: int | float | None) -> ContextManager[Any]: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: IO[bytes], code: int, msg: str,
                         headers: HTTPMessage, newurl: str) -> None:
        return None


class AuthorityClient:
    def __init__(self, url: str, *, policy_provider: RequestPolicyProvider,
                 opener: RequestOpener | None = None) -> None:
        self.url = validate_authority_url(url)
        self._policy_provider = policy_provider
        self._opener = opener if opener is not None else build_opener(ProxyHandler({}), _NoRedirect())

    def _capture(self, *, pagination=False) -> RequestPolicy:
        return _capture_policy(self._policy_provider, pagination=pagination)

    def request(self, method: str, path: str, *, body: WireJSON | None = None,
                query: dict[str, object] | None = None) -> WireJSON:
        return self._request(method, path, body=body, query=query, policy=self._capture())

    def _request(self, method, path, *, body=None, query=None, policy):
        if not isinstance(path, str) or not path.startswith("/v1/") or "?" in path or "#" in path:
            raise AuthorityClientError("invalid_path", "A typed authority path is required")
        endpoint = self.url + path
        if query:
            endpoint += "?" + urlencode({key: value for key, value in query.items() if value is not None})
        request = Request(endpoint, method=method,
                          data=canonical_json_bytes(body) if body is not None else None,
                          headers={"Accept": "application/json", "Content-Type": "application/json"})
        started = time.monotonic()
        try:
            with self._opener.open(request, timeout=policy.timeout_seconds) as response:
                payload = response.read()
        except HTTPError as error:
            try:
                result = parse_json_bytes(error.read())
                if not isinstance(result, dict):
                    raise ValueError("not an error object")
                result = result.get("error", result)
                if not isinstance(result, dict):
                    raise ValueError("not an error object")
                code = result.get("code", "authority_error")
                message = result.get("message", "Authority refused this operation")
                if not isinstance(code, str) or not isinstance(message, str):
                    raise ValueError("invalid error fields")
            except Exception:
                raise AuthorityClientError("authority_error", f"Authority returned HTTP {error.code}") from error
            finally:
                try:
                    error.close()
                except Exception as cleanup_error:
                    error.add_note(f"Secondary HTTP response close failed: {type(cleanup_error).__name__}")
            raise AuthorityClientError(code, message, details=result.get("details")) from error
        except (URLError, OSError, TimeoutError) as error:
            reason = getattr(error, "reason", None)
            raise AuthorityClientError(
                "authority_unavailable",
                "The configured authority is unavailable. Restore the service and read current state before retrying.",
                details={"cause": type(reason if isinstance(reason, BaseException) else error).__name__,
                         "cause_message": str(reason if reason is not None else error)[:200],
                         "elapsed_seconds": round(time.monotonic() - started, 3),
                         "timeout_seconds": policy.timeout_seconds, "method": method, "path": path}) from error
        try:
            return parse_json_bytes(payload)
        except Exception as error:
            raise AuthorityClientError("invalid_response", "Authority returned invalid canonical JSON") from error


def _pages(client, path, *, query, policy):
    after = None
    while True:
        result = client._request("GET", path, query={**(query or {}), "limit": policy.page_size, "after": after}, policy=policy)
        records = result.get("records") if isinstance(result, dict) else None
        next_after = result.get("next_after") if isinstance(result, dict) else None
        if not isinstance(records, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] for row in records
        ):
            raise AuthorityClientError("invalid_response", f"{path} records are malformed")
        if next_after is not None and (not isinstance(next_after, str) or not next_after or
                                       (after is not None and next_after <= after)):
            raise AuthorityClientError("invalid_response", f"{path} pagination did not advance")
        yield result
        if next_after is None:
            return
        after = next_after


def page_records(client: AuthorityClient, path: str, *, query: dict[str, object] | None = None) -> list[dict[str, WireJSON]]:
    policy = client._capture(pagination=True)
    collected = []
    for page in _pages(client, path, query=query, policy=policy):
        collected.extend(page["records"])
    return collected
