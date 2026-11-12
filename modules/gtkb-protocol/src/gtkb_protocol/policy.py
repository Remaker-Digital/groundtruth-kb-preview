"""Explicit consumer-supplied invocation policy, with no value authority here."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
import math

from .errors import AuthorityClientError


@dataclass(frozen=True, kw_only=True)
class RequestPolicy:
    source_id: str
    snapshot_id: str
    timeout_seconds: int | float | None
    page_size: int | None
    unbounded_reason: str | None


RequestPolicyProvider = Callable[[], RequestPolicy]


def _capture_policy(provider: RequestPolicyProvider, *, pagination: bool) -> RequestPolicy:
    if provider is None:
        raise AuthorityClientError("policy_unavailable", "An explicit request policy provider is required")
    if not callable(provider):
        raise AuthorityClientError("invalid_request_policy", "The request policy provider must be callable")
    try:
        policy = provider()
    except AuthorityClientError:
        raise
    except Exception as error:
        raise AuthorityClientError("policy_unavailable", "The request policy supplier failed",
                                   details={"cause": type(error).__name__}) from error
    if policy is None:
        raise AuthorityClientError("policy_unavailable", "The request policy is unavailable")
    if not isinstance(policy, RequestPolicy):
        raise AuthorityClientError("invalid_request_policy", "The supplier did not return a RequestPolicy")
    valid = all(isinstance(value, str) and bool(value.strip())
                for value in (policy.source_id, policy.snapshot_id))
    timeout = policy.timeout_seconds
    if timeout is None:
        valid = valid and isinstance(policy.unbounded_reason, str) and bool(policy.unbounded_reason.strip())
    else:
        try:
            finite = isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and math.isfinite(timeout)
        except (TypeError, OverflowError):
            finite = False
        valid = valid and finite and timeout > 0 and policy.unbounded_reason is None
    size = policy.page_size
    if size is not None:
        valid = valid and isinstance(size, int) and not isinstance(size, bool) and 1 <= size <= 1000
    elif pagination:
        valid = False
    if not valid:
        raise AuthorityClientError("invalid_request_policy", "The supplied request policy is invalid")
    return policy
