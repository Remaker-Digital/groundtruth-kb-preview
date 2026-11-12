"""Explicit AUTH construction inputs. Copyright 2026 Remaker Digital. See NOTICE."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class PostgresSettings:
    """An opaque protected-binding label, never a libpq service/profile name."""

    service: str


@dataclass(frozen=True, kw_only=True)
class AuthorityOperation:
    source_id: str
    snapshot_id: str
    lock_timeout_ms: int
    statement_timeout_ms: int
    connect_poll_seconds: int | float
    repository_probe_seconds: int | float
    read_page_size: int
    deadline: float | None
    checkpoint: Callable[[], None]
    disabled_reason: str | None


ParametersProvider = Callable[[str], Mapping[str, str | int]]
OperationProvider = Callable[[], AuthorityOperation]
