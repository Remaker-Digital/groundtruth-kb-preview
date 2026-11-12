"""Storage-free public GT-KB wire capabilities. Copyright 2026 Remaker Digital."""

__version__ = "0.1.0"

from .codec import canonical_json_bytes, canonical_sha256, parse_json_bytes
from .endpoint import validate_authority_url
from .errors import AuthorityClientError, ProtocolError
from .policy import RequestPolicy, RequestPolicyProvider
from .records import RecordsClient
from .transport import AuthorityClient, RequestOpener, page_records
from .types import (
    ClaimRequest, FenceRequest, FormalLinkIdentifier, HistoryEntry, Identifier,
    Mutation, Record, RecordDomain, RecordHistory, RecordMutation, RecordPage,
    Request, Scope, SessionRequest, Text, Version, WireJSON,
)

__all__ = [
    "__version__", "canonical_json_bytes", "parse_json_bytes", "canonical_sha256",
    "ProtocolError", "AuthorityClientError", "WireJSON", "Identifier",
    "FormalLinkIdentifier", "Text", "Version", "Scope", "Request", "Mutation",
    "SessionRequest", "ClaimRequest", "FenceRequest", "RecordMutation", "Record",
    "HistoryEntry", "RecordHistory", "RecordPage", "RecordDomain",
    "validate_authority_url", "RequestPolicy", "RequestPolicyProvider",
    "AuthorityClient", "RequestOpener", "page_records", "RecordsClient",
]
