# gtkb-protocol

GT-KB's storage-free Python wire library supplies canonical JSON, distinct
protocol/client errors, strict wire envelopes and an explicit HTTP client.
The first candidate is 0.1.0; source presence is not independent acceptance.

gtkb-authority owns authorization, business validation, current/history transactions and
actual native identities. gtkb-home and the assembled product own selected-root configuration and
operational-control resolution. This library imports no database driver,
configuration loader, service, execution adapter or sibling checkout. Repository
references are opaque JSON strings here.

## Public use

Supply AuthorityClient(url, policy_provider=consumer_policy) an explicit HTTP
127.0.0.1 URL and port. The callable returns RequestPolicy with every field
explicit: source_id, snapshot_id, timeout_seconds, page_size, unbounded_reason.
It supplies the consumer's accepted governing policy; this library reads no file or
environment and chooses no timeout/page-size default. Missing/invalid policy
refuses before IO. Explicit timeout None needs a legitimate scoped reason and
original owned ending; it creates no production deadline override.

RecordsClient(client) offers current, history, mutate, list_page and list_all
for terms, specifications, tests, test-plans, test-phases and harnesses. Use
strict RecordMutation(expected_version=..., actor=..., reason=..., fields=...).
Null within fields is distinct from omission. gtkb-authority validates domain fields and
effect rights. Generic client.request remains available for other public paths.

Each operation captures policy once. Pagination keeps that snapshot; the next
operation captures again. The default transport follows no redirect, uses no
ambient proxy, opens once per request and never retries or falls back locally.

canonical_json_bytes, parse_json_bytes and canonical_sha256 preserve the existing
compact sorted UTF-8/LF format, Decimal scale, supported huge integers and Unicode
spelling. Duplicate keys, text/JSON faults, nonfinite numbers and existing range
violations refuse. No RFC 8785 or exact maximum recursive-depth claim is made.

## Build and status

The src-layout build requires hatchling==1.29.0 and runtime pydantic==2.13.3.
Version comes from gtkb_protocol.__version__. With declared build dependencies
installed, run python -m build --no-isolation from this module folder.

This source is a candidate under independent review. It is not an accepted or
released artifact, there is no published package and there is no supported
install. Python metadata is >=3.11; initial build and test evidence is limited
to Windows CPython 3.14.

## License and provenance

Original software uses the unmodified PolyForm Noncommercial License 1.0.0 in
LICENSE. Original documentation and media use the unmodified CC BY-NC 4.0
International text in LICENSE-DOCUMENTATION. NOTICE supplies the Copyright 2026
Remaker Digital, a DBA of VanDusen & Palmeter, LLC attribution and original-work
scope. Third-party dependencies keep their own licenses and notices. See
PROVENANCE.md for the source summary.
