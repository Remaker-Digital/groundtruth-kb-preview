"""Typed public facade for the six ordinary row-shaped record domains."""
from __future__ import annotations

from urllib.parse import quote
from pydantic import TypeAdapter

from .errors import AuthorityClientError
from .transport import AuthorityClient, _pages
from .types import Identifier, Record, RecordDomain, RecordHistory, RecordMutation, RecordPage

_DOMAINS = {"terms", "specifications", "tests", "test-plans", "test-phases", "harnesses"}
_IDENTIFIER = TypeAdapter(Identifier)


def _path(domain, record_id=None):
    if not isinstance(domain, str) or domain not in _DOMAINS:
        raise AuthorityClientError("invalid_request", "A supported plain-record domain is required")
    path = "/v1/" + domain
    if record_id is not None:
        try:
            value = _IDENTIFIER.validate_python(record_id, strict=True)
        except Exception as error:
            raise AuthorityClientError("invalid_request", "A valid record identifier is required") from error
        path += "/" + quote(value, safe="")
    return path


def _filters(filters):
    if filters is None:
        return {}
    if not isinstance(filters, dict) or any(not isinstance(key, str) for key in filters) or {"limit", "after"} & filters.keys():
        raise AuthorityClientError("invalid_request", "Filters must be a string-keyed map without limit or after")
    return dict(filters)


def _response(model, value):
    try:
        return model.model_validate(value)
    except Exception as error:
        raise AuthorityClientError("invalid_response", "Authority returned a malformed record envelope") from error


class RecordsClient:
    def __init__(self, client: AuthorityClient):
        self._client = client

    def current(self, domain: RecordDomain, record_id: str) -> Record:
        path = _path(domain, record_id)
        if record_id is None:
            raise AuthorityClientError("invalid_request", "A record identifier is required")
        return _response(Record, self._client.request("GET", path))

    def history(self, domain: RecordDomain, record_id: str) -> RecordHistory:
        path = _path(domain, record_id)
        if record_id is None:
            raise AuthorityClientError("invalid_request", "A record identifier is required")
        return _response(RecordHistory, self._client.request("GET", path + "/history"))

    def mutate(self, domain: RecordDomain, record_id: str, mutation: RecordMutation) -> Record:
        path = _path(domain, record_id)
        if record_id is None or not isinstance(mutation, RecordMutation):
            raise AuthorityClientError("invalid_request", "A record identifier and strict RecordMutation are required")
        body = mutation.model_dump(mode="python", exclude_unset=True)
        return _response(Record, self._client.request("PUT", path, body=body))

    def list_page(self, domain: RecordDomain, *, after: str | None = None,
                  filters: dict[str, object] | None = None) -> RecordPage:
        path = _path(domain)
        query = _filters(filters)
        if after is not None and (not isinstance(after, str) or not after):
            raise AuthorityClientError("invalid_request", "The pagination cursor must be a nonempty string or null")
        policy = self._client._capture(pagination=True)
        value = self._client._request("GET", path, query={**query, "limit": policy.page_size, "after": after}, policy=policy)
        page = _response(RecordPage, value)
        if page.next_after is not None and after is not None and page.next_after <= after:
            raise AuthorityClientError("invalid_response", "Authority pagination did not advance")
        return page

    def list_all(self, domain: RecordDomain, *, filters: dict[str, object] | None = None) -> list[Record]:
        path = _path(domain)
        query = _filters(filters)
        policy = self._client._capture(pagination=True)
        collected = []
        for value in _pages(self._client, path, query=query, policy=policy):
            collected.extend(_response(RecordPage, value).records)
        return collected
