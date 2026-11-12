"""Original GT-KB software. Copyright 2026 Remaker Digital. See NOTICE."""
from __future__ import annotations

from urllib.parse import urlsplit

from .errors import AuthorityClientError


def validate_authority_url(value: object) -> str:
    """Permit only the installed loopback transport, with no embedded secrets."""
    try:
        if not isinstance(value, str):
            raise TypeError
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port is not None
            and 1 <= parsed.port <= 65535
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except (TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise AuthorityClientError("invalid_endpoint", "authority_url must be an HTTP loopback URL with an explicit port and no credentials")
    return f"http://127.0.0.1:{parsed.port}"
