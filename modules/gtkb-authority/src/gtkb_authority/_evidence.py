"""Typed current TEST evidence selection; no result or authority writer.

Single results and whole-sweep portfolios occupy the same native TEST JSON
column/history. The native consumer must independently authenticate that
current row's protected writer, complete native basis and actual execution.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from .record_types import (
    Digest,
    ExecutedTestSelector,
    Positive,
    QualificationExecutionEvidence,
    Request as Request,
    TargetIdentity,
    Text as Text,
    VerifyAction,
    canonical,
)


class TestEvidenceReference(Request):
    test_id: Text
    test_version: Positive
    execution_evidence_sha256: Digest


class QualificationExecutionPortfolio(Request):
    """One atomic native TEST result, with all its fresh route/surface cells."""

    format: Literal["gtkb.native.qualification.portfolio.v1"] = "gtkb.native.qualification.portfolio.v1"
    test_id: Text
    test_version: Positive
    selector: ExecutedTestSelector
    release_sha256: Digest
    dispatcher_policy_sha256: Digest
    completed_at: str
    entries: list[QualificationExecutionEvidence] = Field(min_length=1)
    references: list[TestEvidenceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def current_atomic_result(self) -> Self:
        VerifyAction.utc_timestamp(self.completed_at)
        keys = [(canonical(row.target.model_dump(mode="json")), row.surface_key) for row in self.entries]
        if keys != sorted(set(keys)):
            raise ValueError("Native portfolio entries are distinct and canonically ordered")
        refs = [row.test_id for row in self.references]
        if refs != sorted(set(refs)) or self.test_id in refs:
            raise ValueError("Current native TEST references are distinct, sorted and nonrecursive")
        for row in self.entries:
            if (
                (row.test_id, row.test_version, row.selector, row.release_sha256)
                != (self.test_id, self.test_version, self.selector, self.release_sha256)
                or row.dispatcher_policy_sha256 != self.dispatcher_policy_sha256
                or datetime.fromisoformat(row.completed_at) > datetime.fromisoformat(self.completed_at)
                or row.surface_key is None
            ):
                raise ValueError("Every portfolio cell belongs to the same current atomic TEST/policy result")
        return self


def qualification_entries(raw: object) -> tuple[QualificationExecutionEvidence, ...]:
    """Decode existing protected result data, never grant acceptance from JSON."""
    if isinstance(raw, dict) and raw.get("format") == "gtkb.native.qualification.portfolio.v1":
        return tuple(QualificationExecutionPortfolio.model_validate(raw).entries)
    return (QualificationExecutionEvidence.model_validate(raw),)


def qualification_entry(
    raw: object, target: TargetIdentity, *, surface_key: str | None = None
) -> QualificationExecutionEvidence:
    selected = [row for row in qualification_entries(raw) if row.target == target]
    if surface_key is not None:
        selected = [row for row in selected if row.surface_key == surface_key]
    if len(selected) != 1:
        raise ValueError("Select one exact qualified target/surface; ambiguity grants no launch")
    return selected[0]


def portfolio_policy(raw: object) -> str | None:
    if isinstance(raw, dict) and raw.get("format") == "gtkb.native.qualification.portfolio.v1":
        return QualificationExecutionPortfolio.model_validate(raw).dispatcher_policy_sha256
    return None


def portfolio_result_identity(raw: object) -> tuple[str, int, str]:
    if isinstance(raw, dict) and raw.get("format") == "gtkb.native.qualification.portfolio.v1":
        value: QualificationExecutionPortfolio | QualificationExecutionEvidence = (
            QualificationExecutionPortfolio.model_validate(raw)
        )
    else:
        value = QualificationExecutionEvidence.model_validate(raw)
    return value.test_id, value.test_version, value.completed_at
