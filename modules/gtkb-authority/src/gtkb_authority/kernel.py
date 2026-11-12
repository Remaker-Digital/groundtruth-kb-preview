"""Native PostgreSQL domain transactions and current-state migration boundary.

This module has no import-time database effects. Operator initialization and
migration use ``gt db postgres``. Ordinary clients use the typed domain service;
they do not receive these internal storage primitives or database credentials.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import select
import sqlite3
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.errors import (
    CheckViolation,
    DataError,
    DeadlockDetected,
    ForeignKeyViolation,
    LockNotAvailable,
    NotNullViolation,
    SerializationFailure,
    UniqueViolation,
)
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from gtkb_protocol import ProtocolError as PostgresKernelError, canonical_json_bytes, canonical_sha256, parse_json_bytes
from .settings import PostgresSettings, ParametersProvider, OperationProvider
from ._lifetime import OperationOwner, operation_method
from ._deadlines import Deadline, checked, earliest, expired, remaining
from .catalog import ApplicationRegistryError, validate_application_scope
from .planning import PlanningError, validate_estimate

_LOG = logging.getLogger(__name__)


def _unclassified_failure(message: str, cause: BaseException) -> PostgresKernelError:
    """Return the credential-free public error for a failure no narrower code covers, after logging only its type under
    an incident reference the caller receives, so an operator can find it in the process's own log (for the
    authority service, the service log). No arbitrary exception text or chain is retained in diagnostics."""
    incident = uuid.uuid4().hex
    _LOG.error("postgres_operation_failed incident %s type=%s", incident, type(cause).__name__)
    return PostgresKernelError("postgres_operation_failed", message, details={"incident": incident})


SCHEMA_VERSION = 1
SCHEMA_FORMAT = "gtkb.postgresql.schema.v1"
# Exact installed predecessor for the approved repository/scope transition.
# This is an explicit administration precondition, never a startup/read alias.
REPOSITORY_SCOPE_PREDECESSOR_SHA256 = "2f25071544591b01b44e0da491a19bd9adee509627114a4dad528205a8f6e2ca"
# c123 (batch design WP5 G17): the c121 and c122 releases' schema, before storage constrained the bridge statuses.
BRIDGE_STATUS_PREDECESSOR_SHA256 = "4b5f8275cec878ade811adf5692436835ef06c147d79a24686a29dad327980fb"
DISPATCHER_PREDECESSOR_SHA256 = "55e4eb33ebb058d35fc354a67e5596518bc06e62320b59fe20229ecd57792989"
EXECUTION_CARRIER_PREDECESSOR_SHA256 = "34d5c61fff3de2b00b4e63b2f99b6f1a17a7ab46b098ef2872909e01422a2a73"
PREPARED_HOLDER_PREDECESSOR_SHA256 = "2dc3b125d3f319feea1479b69240eea85c08f90f56d03b5d1f013e6ba544ab79"
CORE_INIT_SUBJECT_SCHEMA_SHA256 = "5e3cfeb97da3df40bdca50fa89b2889fc5039a115c261a6d695461389a0ec6a5"
DISPATCHER_INIT_SUBJECT_PREDECESSOR_SHA256 = "85c883545be5580b1e96b09e7167d57ed5ab6c9b9399bb2b6afae1844411d69a"
EXPLICIT_SCOPE_PREDECESSOR_SHA256 = "abccc874ad2b8a662c836599a130947285db2451f8adb30a3b83b79304760642"
# Each supported predecessor and the steps, in order and in one transaction, that bring it to this release. Every
# step's checks run before any step's DDL, so a refused transition changes nothing.
INIT_SUBJECT_PREDECESSOR_SHA256 = "55e4eb33ebb058d35fc354a67e5596518bc06e62320b59fe20229ecd57792989"
SCHEMA_TRANSITIONS: dict[str, tuple[str, ...]] = {
    REPOSITORY_SCOPE_PREDECESSOR_SHA256: (
        "repository_scope",
        "bridge_status",
        "dispatcher_coordination",
        "execution_carriers",
        "prepared_holder",
        "init_subject",
    ),
    BRIDGE_STATUS_PREDECESSOR_SHA256: (
        "bridge_status",
        "dispatcher_coordination",
        "execution_carriers",
        "prepared_holder",
        "init_subject",
    ),
    DISPATCHER_PREDECESSOR_SHA256: ("dispatcher_coordination", "execution_carriers", "prepared_holder", "init_subject"),
    EXECUTION_CARRIER_PREDECESSOR_SHA256: ("execution_carriers", "prepared_holder", "init_subject"),
    PREPARED_HOLDER_PREDECESSOR_SHA256: ("prepared_holder", "init_subject"),
    CORE_INIT_SUBJECT_SCHEMA_SHA256: ("dispatcher_coordination", "execution_carriers", "prepared_holder"),
    DISPATCHER_INIT_SUBJECT_PREDECESSOR_SHA256: ("init_subject",),
}
# Scope is additive to the selected dispatcher composition, not an alternative
# coordination schema. Every recognized predecessor retains its existing steps.
SCHEMA_TRANSITIONS = {key: (*steps, "explicit_scope") for key, steps in SCHEMA_TRANSITIONS.items()}
SCHEMA_TRANSITIONS[EXPLICIT_SCOPE_PREDECESSOR_SHA256] = ("explicit_scope",)
PLANNING_ESTIMATE_PREDECESSOR_SHA256 = "2de23b7461bfe1e95b24692034adb58fdef3c531e1a3433e5842b6567f815535"
SCHEMA_TRANSITIONS = {key: (*steps, "planning_estimate") for key, steps in SCHEMA_TRANSITIONS.items()}
SCHEMA_TRANSITIONS[PLANNING_ESTIMATE_PREDECESSOR_SHA256] = ("planning_estimate",)
METER_USAGE_BASIS_PREDECESSOR_SHA256 = "8b65e04ff7aa6d31d699c84cb2c6e4a8ac5e39e6cc7864215166000cddcf3da1"
SCHEMA_TRANSITIONS = {key: (*steps, "meter_usage_basis") for key, steps in SCHEMA_TRANSITIONS.items()}
SCHEMA_TRANSITIONS[METER_USAGE_BASIS_PREDECESSOR_SHA256] = ("meter_usage_basis",)
# The bridge status columns the status step constrains, with the column that names each row's attempt.
BRIDGE_STATUS_COLUMNS = (
    ("bridge_attempts", "head_status", "id"),
    ("bridge_items", "status", "attempt_id"),
    ("work_intent_claims", "intended_status", "attempt_id"),
)
# PostgreSQL ships this exact comment on the stock ``public`` schema of a freshly created
# database. It is the absence of kernel metadata, not drift from it, so a table-free ``public``
# schema carrying exactly this string is an uninitialized target (WI-7690). The match is exact:
# no stripping, case folding, JSON fallback, or wildcard, and it applies to no other schema.
STOCK_PUBLIC_SCHEMA_COMMENT = "standard public schema"
TRANSFORM_FORMAT = "gtkb.postgresql.transform.v1"
CURRENT_FORMAT = "gtkb.postgresql.current.v1"
SQLITE_INVENTORY_FORMAT = "gtkb.sqlite.table-inventory.v1"
PG_INTEGER_MIN = -(2**31)
PG_INTEGER_MAX = 2**31 - 1

CURRENT_TABLES = (
    "specifications",
    "specification_deliberation_sources",
    "test_procedures",
    "operational_procedures",
    "environment_config",
    "documents",
    "tests",
    "test_plans",
    "test_plan_phases",
    "work_items",
    "projects",
    "project_work_item_memberships",
    "project_dependencies",
    "project_artifact_links",
    "testable_elements",
    "deliberations",
    "deliberation_specs",
    "deliberation_work_items",
    "canonical_terms",
    "harnesses",
)
APPLICATION_SCOPE_TABLES = frozenset(CURRENT_TABLES)
_NEW_APPLICATION_SCOPE_TABLES = APPLICATION_SCOPE_TABLES - {"specifications", "tests"}
PRE_DISPATCHER_COORDINATION_TABLES = ("session_init_bindings", "bridge_attempts", "bridge_items", "work_intent_claims")
COORDINATION_TABLES = (*PRE_DISPATCHER_COORDINATION_TABLES, "bridge_outbox", "dispatcher_meter")
MIGRATION_TABLES = (*CURRENT_TABLES, "session_init_bindings")
BINDING_COLUMNS = (
    "native_context_id",
    "session_context_id",
    "subject",
    "role",
    "created_at",
    "minimum_idempotency_identity",
    "execution_observation",
)
ALL_TABLES = (*CURRENT_TABLES, "record_history", *COORDINATION_TABLES)
PRE_DISPATCHER_TABLES = (*CURRENT_TABLES, "record_history", *PRE_DISPATCHER_COORDINATION_TABLES)

REBUILT_LATER_TABLES = frozenset(
    {
        "assertion_runs",
        "pipeline_events",
        "sot_quarantine_receipts",
        "work_intent_claims",
    }
)
RETIRED_TABLES = frozenset(
    {
        "session_prompts",
        "dispatch_events",
        "test_coverage",
        "project_authorizations",
        "flow_definitions",
        "flow_instances",
        "stage_instances",
        "flow_events",
        "flow_artifacts",
        "stage_leases",
        "stage_attempt_telemetry",
        "agent_capability_snapshots",
        "dispatch_lanes",
        "dispatch_lane_score_dimensions",
        "dispatch_lane_scoring_evidence",
        "dispatch_lane_score_snapshots",
        "dispatch_lane_projection_snapshots",
        "backlog_snapshots",
        "quality_scores",
        "spec_quality_scores",
        "session_snapshots",
        "sot_registry_observation_capabilities",
        # Obsolete dispatcher projections/metrics; no worker role survives in a lane.
        "dispatch_default_metric_events",
        "dispatch_default_metrics_snapshots",
        "dispatch_lane_matrix",
        "dispatch_lane_projection_metadata",
        # Historical bootstrap permission/effect bundles. Current formal, project,
        # work and test facts migrate through their domain tables; backups retain history.
        "emergency_bootstrap_operational_event_tombstones",
        "emergency_bootstrap_operational_events",
        "governed_operational_events",
        "operational_event_tombstones",
        "operational_event_versions",
        "operational_events",
        # Mutable session containers and duplicate role provenance are not bindings.
        "session_context_envelope_terminal_facts",
        "session_context_envelopes",
        "session_role_attestations",
        # Canonical registry declarations stay in TOML; no projection or
        # observation journal is rebuilt in PostgreSQL.
        "sot_artifacts",
        "sot_artifact_revisions",
        "sot_registry_transaction_journal",
        # Retired per-effect permission and recovery receipts.
        "sot_registry_bridge_publication_capabilities",
        "sot_registry_bridge_recovery_receipts",
        "sot_registry_transition_requests",
        "test_artifact_update_requests",
    }
)
SOURCE_TABLES = frozenset(MIGRATION_TABLES) | REBUILT_LATER_TABLES | RETIRED_TABLES
FORBIDDEN_TABLES = (
    REBUILT_LATER_TABLES | RETIRED_TABLES | frozenset({"authorization_packets", "approval_receipts", "record_heads"})
) - frozenset(COORDINATION_TABLES)
FORBIDDEN_COLUMNS = frozenset(
    {
        "approval_state",
        "membership_role",
        "reviewer_precedence",
        "authorization_name",
        "owner_decision_deliberation_id",
        "related_bridge_threads",
    }
)
_CHANGE_COLUMNS = ("changed_by", "changed_at", "change_reason")
_TIMESTAMP_COLUMNS = frozenset(
    {
        "added_at",
        "changed_at",
        "completed_at",
        "implementation_verified_at",
        "last_corrected_at",
        "last_executed_at",
        "last_verified_at",
        "retired_at",
    }
)
_DATE_COLUMNS = frozenset({"start_date", "target_date", "last_executed_on", "last_verified_on", "last_corrected_on"})
_DATE_PRECISION_FIELDS = {
    "tests": (("last_executed_at", "last_executed_on"),),
    "test_procedures": (("last_executed_at", "last_executed_on"),),
    "test_plan_phases": (("last_executed_at", "last_executed_on"),),
    "operational_procedures": (
        ("last_verified_at", "last_verified_on"),
        ("last_corrected_at", "last_corrected_on"),
    ),
}
_INTEGER_COLUMNS = frozenset(
    {
        "assertion_count",
        "implementation_order",
        "membership_order",
        "phase_order",
        "rank",
        "registry_version",
        "version",
    }
)
_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "specifications": frozenset({"id", "version", "title", "status", *_CHANGE_COLUMNS}),
    "specification_deliberation_sources": frozenset({"spec_id", "deliberation_id", "version", "added_at", "added_by"}),
    "test_procedures": frozenset({"id", "version", "title", *_CHANGE_COLUMNS}),
    "operational_procedures": frozenset({"id", "version", "title", *_CHANGE_COLUMNS}),
    "environment_config": frozenset(
        {"id", "version", "environment", "category", "key", "value", "sensitive", *_CHANGE_COLUMNS}
    ),
    "documents": frozenset({"id", "version", "title", "category", "status", *_CHANGE_COLUMNS}),
    "tests": frozenset({"id", "version", "title", "spec_id", "test_type", "expected_outcome", *_CHANGE_COLUMNS}),
    "test_plans": frozenset({"id", "version", "title", "status", *_CHANGE_COLUMNS}),
    "test_plan_phases": frozenset(
        {"id", "version", "plan_id", "phase_order", "title", "gate_criteria", *_CHANGE_COLUMNS}
    ),
    "work_items": frozenset(
        {"id", "version", "title", "origin", "component", "resolution_status", "stage", *_CHANGE_COLUMNS}
    ),
    "projects": frozenset({"id", "version", "name", "kind", "status", *_CHANGE_COLUMNS}),
    "project_work_item_memberships": frozenset(
        {"id", "version", "project_id", "work_item_id", "status", *_CHANGE_COLUMNS}
    ),
    "project_dependencies": frozenset(
        {
            "id",
            "version",
            "dependent_project_id",
            "prerequisite_project_id",
            "dependency_kind",
            "required_prerequisite_state",
            "affected_gate",
            "provenance",
            "registry_version",
            "blocking_status",
            "status",
            *_CHANGE_COLUMNS,
        }
    ),
    "project_artifact_links": frozenset(
        {
            "id",
            "version",
            "project_id",
            "artifact_type",
            "artifact_ref",
            "relationship",
            "status",
            *_CHANGE_COLUMNS,
        }
    ),
    "testable_elements": frozenset(
        {
            "id",
            "version",
            "subsystem",
            "page_or_module",
            "name",
            "element_type",
            "expected_behavior",
            "applicable_dimensions",
            "status",
            *_CHANGE_COLUMNS,
        }
    ),
    "deliberations": frozenset({"id", "version", "source_type", "title", "summary", "content", *_CHANGE_COLUMNS}),
    "deliberation_specs": frozenset({"deliberation_id", "spec_id", "version"}),
    "deliberation_work_items": frozenset({"deliberation_id", "work_item_id", "version"}),
    "canonical_terms": frozenset(
        {
            "id",
            "version",
            "canonical_term",
            "definition",
            "authority_level",
            "scope",
            "lifecycle_status",
            "source_authority",
            *_CHANGE_COLUMNS,
        }
    ),
    "harnesses": frozenset({"id", "version", "harness_name", "harness_type", "status", *_CHANGE_COLUMNS}),
}


@dataclass(frozen=True)
class TableSpec:
    name: str
    identity_columns: tuple[str, ...]
    columns: tuple[str, ...]
    json_columns: frozenset[str] = frozenset()
    boolean_columns: frozenset[str] = frozenset()


def _table(
    name: str,
    identity: Sequence[str],
    columns: Sequence[str],
    *,
    json_columns: Iterable[str] = (),
    boolean_columns: Iterable[str] = (),
) -> TableSpec:
    if name in APPLICATION_SCOPE_TABLES and "application_scope" not in columns:
        columns = (*columns, "application_scope")
    return TableSpec(
        name=name,
        identity_columns=tuple(identity),
        columns=tuple(columns),
        json_columns=frozenset(json_columns),
        boolean_columns=frozenset(boolean_columns),
    )


TABLE_SPECS: dict[str, TableSpec] = {
    "specifications": _table(
        "specifications",
        ("id",),
        (
            "id",
            "version",
            "title",
            "description",
            "priority",
            "scope",
            "section",
            "handle",
            "tags",
            "status",
            "assertions",
            "type",
            "authority",
            "provisional_until",
            "constraints",
            "affected_by",
            "testability",
            "source_paths",
            "implementation_verified_at",
            "retired_at",
            "parent",
            "application_scope",
            *_CHANGE_COLUMNS,
        ),
        json_columns=("tags", "assertions", "constraints", "affected_by", "source_paths"),
    ),
    "specification_deliberation_sources": _table(
        "specification_deliberation_sources",
        ("spec_id", "deliberation_id"),
        ("spec_id", "deliberation_id", "version", "source_role", "added_at", "added_by"),
    ),
    "test_procedures": _table(
        "test_procedures",
        ("id",),
        (
            "id",
            "version",
            "title",
            "type",
            "content",
            "assertion_count",
            "last_execution_status",
            "last_executed_at",
            "last_executed_on",
            *_CHANGE_COLUMNS,
        ),
    ),
    "operational_procedures": _table(
        "operational_procedures",
        ("id",),
        (
            "id",
            "version",
            "title",
            "type",
            "variables",
            "steps",
            "known_failure_modes",
            "last_verified_at",
            "last_verified_on",
            "last_corrected_at",
            "last_corrected_on",
            *_CHANGE_COLUMNS,
        ),
        json_columns=("variables", "steps", "known_failure_modes"),
    ),
    "environment_config": _table(
        "environment_config",
        ("id",),
        ("id", "version", "environment", "category", "key", "value", "sensitive", "notes", *_CHANGE_COLUMNS),
        boolean_columns=("sensitive",),
    ),
    "documents": _table(
        "documents",
        ("id",),
        ("id", "version", "title", "category", "content", "tags", "status", "source_path", *_CHANGE_COLUMNS),
        json_columns=("tags",),
    ),
    "tests": _table(
        "tests",
        ("id",),
        (
            "id",
            "version",
            "title",
            "spec_id",
            "test_type",
            "test_file",
            "test_class",
            "test_function",
            "description",
            "expected_outcome",
            "last_result",
            "last_executed_at",
            "last_executed_on",
            "application_scope",
            *_CHANGE_COLUMNS,
            "execution_evidence",
        ),
        json_columns=("execution_evidence",),
    ),
    "test_plans": _table(
        "test_plans",
        ("id",),
        ("id", "version", "title", "description", "status", *_CHANGE_COLUMNS),
    ),
    "test_plan_phases": _table(
        "test_plan_phases",
        ("id",),
        (
            "id",
            "version",
            "plan_id",
            "phase_order",
            "title",
            "description",
            "gate_criteria",
            "test_ids",
            "last_result",
            "last_executed_at",
            "last_executed_on",
            *_CHANGE_COLUMNS,
        ),
        json_columns=("test_ids",),
    ),
    "work_items": _table(
        "work_items",
        ("id",),
        (
            "id",
            "version",
            "title",
            "description",
            "origin",
            "component",
            "source_spec_id",
            "source_test_id",
            "failure_description",
            "resolution_status",
            "priority",
            "stage",
            "implementation_order",
            "status_detail",
            "source_owner_directive",
            "source_deliberation_query",
            "related_deliberation_ids",
            "related_spec_ids_at_creation",
            "depends_on_work_items",
            "blocks_work_items",
            "acceptance_summary",
            "regression_visibility",
            "completion_evidence",
            "supersedes",
            "superseded_by",
            *_CHANGE_COLUMNS,
        ),
        json_columns=(
            "related_deliberation_ids",
            "related_spec_ids_at_creation",
            "depends_on_work_items",
            "blocks_work_items",
            "supersedes",
            "superseded_by",
        ),
    ),
    "projects": _table(
        "projects",
        ("id",),
        (
            "id",
            "version",
            "name",
            "kind",
            "status",
            "authorization",
            "repository_ref",
            "rank",
            "parent_project_id",
            "purpose",
            "target_outcome",
            "scope_note",
            "start_date",
            "target_date",
            "completed_at",
            "notes",
            "source_project_name",
            "source_subproject_name",
            *_CHANGE_COLUMNS,
            "application_scope",
            "planning_estimate",
        ),
        json_columns=("planning_estimate",),
    ),
    "project_work_item_memberships": _table(
        "project_work_item_memberships",
        ("id",),
        (
            "id",
            "version",
            "project_id",
            "work_item_id",
            "membership_order",
            "status",
            "source",
            *_CHANGE_COLUMNS,
        ),
    ),
    "project_dependencies": _table(
        "project_dependencies",
        ("id",),
        (
            "id",
            "version",
            "dependent_project_id",
            "prerequisite_project_id",
            "dependency_kind",
            "required_prerequisite_state",
            "affected_gate",
            "provenance",
            "registry_version",
            "rationale",
            "blocking_status",
            "related_work_item_id",
            "status",
            *_CHANGE_COLUMNS,
        ),
    ),
    "project_artifact_links": _table(
        "project_artifact_links",
        ("id",),
        (
            "id",
            "version",
            "project_id",
            "artifact_type",
            "artifact_ref",
            "relationship",
            "status",
            "notes",
            *_CHANGE_COLUMNS,
        ),
    ),
    "testable_elements": _table(
        "testable_elements",
        ("id",),
        (
            "id",
            "version",
            "subsystem",
            "page_or_module",
            "name",
            "element_type",
            "expected_behavior",
            "spec_id",
            "applicable_dimensions",
            "status",
            *_CHANGE_COLUMNS,
        ),
        json_columns=("applicable_dimensions",),
    ),
    "deliberations": _table(
        "deliberations",
        ("id",),
        (
            "id",
            "version",
            "spec_id",
            "work_item_id",
            "source_type",
            "source_ref",
            "title",
            "summary",
            "content",
            "content_hash",
            "participants",
            "outcome",
            "session_id",
            "sensitivity",
            "redaction_state",
            "redaction_notes",
            "origin_project",
            "origin_repo",
            *_CHANGE_COLUMNS,
        ),
        json_columns=("participants",),
    ),
    "deliberation_specs": _table(
        "deliberation_specs",
        ("deliberation_id", "spec_id"),
        ("deliberation_id", "spec_id", "version", "role"),
    ),
    "deliberation_work_items": _table(
        "deliberation_work_items",
        ("deliberation_id", "work_item_id"),
        ("deliberation_id", "work_item_id", "version", "role"),
    ),
    "canonical_terms": _table(
        "canonical_terms",
        ("id",),
        (
            "id",
            "version",
            "canonical_term",
            "definition",
            "authority_level",
            "scope",
            "accepted_synonyms",
            "discouraged_synonyms",
            "linked_artifacts",
            "linked_services",
            "usage_examples",
            "forbidden_uses",
            "lifecycle_status",
            "source_authority",
            *_CHANGE_COLUMNS,
        ),
        json_columns=(
            "accepted_synonyms",
            "discouraged_synonyms",
            "linked_artifacts",
            "linked_services",
            "usage_examples",
            "forbidden_uses",
        ),
    ),
    "harnesses": _table(
        "harnesses",
        ("id",),
        (
            "id",
            "version",
            "harness_name",
            "harness_type",
            "status",
            "invocation_surfaces",
            "capabilities_ref",
            *_CHANGE_COLUMNS,
        ),
        json_columns=("invocation_surfaces",),
    ),
}


def _validate_text(value: str, *, label: str = "text") -> None:
    if "\x00" in value:
        raise PostgresKernelError("invalid_text", f"{label} contains NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PostgresKernelError("invalid_text", f"{label} contains an unpaired surrogate") from exc




def _postgres_json_dumps(value: object) -> str:
    """Serialize JSONB inputs with the same exact numeric contract as manifests."""
    return canonical_json_bytes(value).decode("utf-8").removesuffix("\n")


def _canonical_sql_resource_bytes(raw: bytes) -> bytes:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise PostgresKernelError("schema_resource_invalid", "Packaged PostgreSQL schema has a UTF-8 BOM")
    normalized = raw.replace(b"\r\n", b"\n")
    if b"\r" in normalized:
        raise PostgresKernelError("schema_resource_invalid", "Packaged PostgreSQL schema has a bare CR")
    return normalized


def schema_sql_bytes() -> bytes:
    return _canonical_sql_resource_bytes(files("gtkb_authority").joinpath("postgresql_v1.sql").read_bytes())


def meter_usage_basis_column_sql() -> bytes:
    """Reuse the fresh schema's exact nullable column and constraint for upgrade."""
    resource = schema_sql_bytes()
    start = resource.index(b"    -- METER USAGE BASIS BEGIN\n") + len(b"    -- METER USAGE BASIS BEGIN\n")
    end = resource.index(b"    -- METER USAGE BASIS END\n", start)
    return resource[start:end].strip().removesuffix(b",")


def _approved_meter_exception_preimage(cursor: Any, schema_name: str) -> bool:
    """Exact owner exception 2026-10-25; no other legacy unknown is classified."""
    from .record_types import ExecutionMeterOwner, ExecutionObservation

    native = "f550d42c-560f-40d6-a2c8-abd74b2bc8b3"
    digest = "508ba56bf5d594d9d21093b76e86c2b629e7f7e5e129bb85bb6081d53ee29149"
    cursor.execute(
        sql.SQL(
            "SELECT * FROM {}.dispatcher_meter WHERE execution_native_context_id=%s "
            "AND try_no=1 AND call_no=42 AND unit IN ('tokens','micro_usd') FOR UPDATE"
        ).format(sql.Identifier(schema_name)),
        (native,),
    )
    rows = cursor.fetchall()
    cursor.execute(
        sql.SQL(
            "SELECT execution_observation FROM {}.session_init_bindings WHERE native_context_id=%s FOR UPDATE"
        ).format(sql.Identifier(schema_name)),
        (native,),
    )
    binding = cursor.fetchone()
    if not rows and binding is None:
        return False
    valid = binding is not None and len(rows) == 2 and {row["unit"] for row in rows} == {"tokens", "micro_usd"}
    try:
        owner = ExecutionMeterOwner.decode(rows[0]["basis_key"])
        observation = ExecutionObservation.model_validate(binding["execution_observation"] if binding else None)
        valid = valid and (
            observation.native_context_id == native == owner.native_context_id
            and observation.execution_id == owner.execution_id == "15d8cc80-7530-4efc-9fc2-d44ce8a7805a"
            and observation.enrolled_at == "2026-10-25T07:57:01.098665+00:00"
            and observation.ended_at == "2026-10-25T08:08:24.792848+00:00"
            and observation.provider_id == "openrouter"
            and observation.target == owner.target
            and observation.release_sha256 == "e2036396585923661dad3a20119064f0903464bb7be284d7d87525d0c62adcb5"
        )
    except (ValueError, TypeError, KeyError, IndexError):
        valid = False
    for row in rows:
        amount = {"tokens": 2_008_192, "micro_usd": 2_000_000}[row["unit"]]
        valid = valid and (
            row["basis_sha256"] == digest == hashlib.sha256(row["basis_key"].encode()).hexdigest()
            and row["basis_key"] == rows[0]["basis_key"]
            and row["attempt_id"] is None
            and row["dimension_key"] == canonical_json_bytes(["global", "all", row["unit"]]).decode().strip()
            and (row["window_start_ms"], row["window_end_ms"]) == (1_792_915_200_000, 1_792_915_800_000)
            and (row["reserved_amount"], row["held_amount"], row["consumed_amount"], row["settlement_state"])
            == (amount, amount, 0, "unknown")
        )
    if not valid:
        raise PostgresKernelError(
            "schema_upgrade_preimage_mismatch", "The approved single-call meter exception changed"
        )
    return True


def dispatcher_coordination_sql() -> bytes:
    """The same declarative DDL initializes and explicitly upgrades coordination."""
    resource = schema_sql_bytes()
    start = resource.index(b"-- DN-A BEGIN:")
    end = resource.index(b"-- DN-A END\n", start) + len(b"-- DN-A END\n")
    return resource[start:end]


def dispatch_observation_check_sql() -> bytes:
    resource = schema_sql_bytes()
    start = resource.index(
        b"ALTER TABLE {schema}.bridge_attempts ADD CONSTRAINT bridge_dispatch_observation_check CHECK ("
    )
    end = resource.index(b"\n);\n", start) + len(b"\n);\n")
    return resource[start:end]


def schema_sql_sha256() -> str:
    return hashlib.sha256(schema_sql_bytes()).hexdigest()


def bridge_status_check_values() -> tuple[str, ...]:
    """The statuses the schema resource's three bridge status CHECKs admit (c123; batch design WP5 G17).

    The status step of a transition adds exactly these, so an upgraded catalog constrains what a fresh one does. The
    resource is the one source: the schema hash covers its bytes, and a list generated at run time could change a
    fresh catalog without changing the hash. Tests bind it to bridge/vocabulary.py's CANONICAL_STATUSES.
    """
    text = schema_sql_bytes().decode("utf-8")
    lists: list[tuple[str, ...]] = []
    for table, column, _key in BRIDGE_STATUS_COLUMNS:
        block = re.search(rf"CREATE TABLE \{{schema\}}\.{table} \((.*?)\n\);", text, re.DOTALL)
        check = (
            re.search(rf"\n    {column} TEXT[^,]*?CHECK \({column} IN \(([^)]*)\)\)", block.group(1), re.DOTALL)
            if block
            else None
        )
        lists.append(tuple(re.findall(r"'([^']*)'", check.group(1))) if check else ())
    if not lists[0] or any(values != lists[0] for values in lists):
        raise PostgresKernelError(
            "schema_resource_invalid", "Packaged PostgreSQL schema does not list one bridge status set"
        )
    return lists[0]


def _schema_metadata(*, catalog_sha256: str) -> dict[str, Any]:
    return {
        "catalog_sha256": catalog_sha256,
        "format": SCHEMA_FORMAT,
        "schema_sha256": schema_sql_sha256(),
        "schema_version": SCHEMA_VERSION,
    }


def _ascii_lower(value: str) -> str:
    return "".join(chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in value)


def _strict_bool(value: object, *, label: str) -> bool:
    if type(value) is not int or value not in (0, 1):
        raise PostgresKernelError("invalid_sqlite_inventory", f"{label} must be SQLite boolean 0 or 1")
    return bool(value)


def normalize_table_inventory(
    table_list_rows: Sequence[Mapping[str, Any]],
    column_rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Validate and normalize already-read SQLite inventory rows without I/O."""
    seen_exact: set[str] = set()
    seen_folded: set[str] = set()
    tables: list[dict[str, Any]] = []
    for raw_table in table_list_rows:
        name = raw_table.get("name")
        kind = raw_table.get("type")
        if not isinstance(name, str) or not name or not isinstance(kind, str):
            raise PostgresKernelError("invalid_sqlite_inventory", "SQLite table name and kind must be text")
        _validate_text(name, label="SQLite table name")
        if name in seen_exact or _ascii_lower(name) in seen_folded:
            raise PostgresKernelError("invalid_sqlite_inventory", f"Duplicate SQLite table name: {name}")
        if kind not in {"table", "virtual", "shadow"}:
            raise PostgresKernelError("invalid_sqlite_inventory", f"Unsupported SQLite table kind: {kind}")
        seen_exact.add(name)
        seen_folded.add(_ascii_lower(name))

        raw_columns = list(column_rows.get(name, ()))
        expected_count = raw_table.get("ncol")
        if (
            not isinstance(expected_count, int)
            or isinstance(expected_count, bool)
            or expected_count != len(raw_columns)
        ):
            raise PostgresKernelError("invalid_sqlite_inventory", f"Column-count disagreement for SQLite table: {name}")
        normalized_columns: list[dict[str, Any]] = []
        seen_column_exact: set[str] = set()
        seen_column_folded: set[str] = set()
        cids: list[int] = []
        for raw_column in raw_columns:
            cid = raw_column.get("cid")
            column_name = raw_column.get("name")
            declared_type = raw_column.get("type")
            default_sql = raw_column.get("dflt_value")
            hidden = raw_column.get("hidden")
            primary_key = raw_column.get("pk")
            if not isinstance(cid, int) or isinstance(cid, bool) or cid < 0:
                raise PostgresKernelError("invalid_sqlite_inventory", f"Invalid column id in SQLite table: {name}")
            if not isinstance(column_name, str) or not isinstance(declared_type, str):
                raise PostgresKernelError("invalid_sqlite_inventory", f"Invalid column text in SQLite table: {name}")
            _validate_text(column_name, label="SQLite column name")
            _validate_text(declared_type, label="SQLite declared type")
            if default_sql is not None:
                if not isinstance(default_sql, str):
                    raise PostgresKernelError(
                        "invalid_sqlite_inventory", f"Invalid default SQL in SQLite table: {name}"
                    )
                _validate_text(default_sql, label="SQLite default SQL")
            if column_name in seen_column_exact or _ascii_lower(column_name) in seen_column_folded:
                raise PostgresKernelError("invalid_sqlite_inventory", f"Duplicate SQLite column name in table: {name}")
            if not isinstance(hidden, int) or isinstance(hidden, bool) or hidden not in range(4):
                raise PostgresKernelError("invalid_sqlite_inventory", f"Invalid hidden flag in SQLite table: {name}")
            if not isinstance(primary_key, int) or isinstance(primary_key, bool) or primary_key < 0:
                raise PostgresKernelError(
                    "invalid_sqlite_inventory", f"Invalid primary-key ordinal in SQLite table: {name}"
                )
            seen_column_exact.add(column_name)
            seen_column_folded.add(_ascii_lower(column_name))
            cids.append(cid)
            normalized_columns.append(
                {
                    "cid": cid,
                    "declared_type": declared_type,
                    "default_sql": default_sql,
                    "hidden": hidden,
                    "name": column_name,
                    "not_null": _strict_bool(raw_column.get("notnull"), label="not_null"),
                    "primary_key_ordinal": primary_key,
                }
            )
        if sorted(cids) != list(range(len(raw_columns))):
            raise PostgresKernelError("invalid_sqlite_inventory", f"Noncontiguous column ids in SQLite table: {name}")
        normalized_columns.sort(key=lambda row: row["cid"])
        tables.append(
            {
                "columns": normalized_columns,
                "kind": kind,
                "name": name,
                "strict": _strict_bool(raw_table.get("strict"), label="strict"),
                "without_rowid": _strict_bool(raw_table.get("wr"), label="without_rowid"),
            }
        )
    tables.sort(key=lambda row: (str(row["name"]).encode("utf-8"), str(row["kind"])))
    return {"format": SQLITE_INVENTORY_FORMAT, "tables": tables}


_LOWER_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


def _exact_keys(value: object, expected: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PostgresKernelError("invalid_contract", f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        raise PostgresKernelError(
            "invalid_contract",
            f"{label} has missing or unknown keys",
            details={"missing": sorted(expected - actual), "unknown": sorted(actual - expected)},
        )
    return value


def _nonnegative_int(value: object, *, label: str, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < (1 if positive else 0):
        qualifier = "positive" if positive else "nonnegative"
        raise PostgresKernelError("invalid_contract", f"{label} must be a {qualifier} integer")
    return value


def _opaque_id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PostgresKernelError("invalid_contract", f"{label} must be a non-empty opaque string")
    _validate_text(value, label=label)
    return value


def validate_transform_plan(value: object) -> dict[str, Any]:
    plan = _exact_keys(value, {"format", "schema_version", "source", "projects", "project_dependencies"}, label="plan")
    if (
        plan["format"] != TRANSFORM_FORMAT
        or type(plan["schema_version"]) is not int
        or plan["schema_version"] != SCHEMA_VERSION
    ):
        raise PostgresKernelError("invalid_transform_plan", "Unsupported transform-plan format or schema version")

    source = _exact_keys(
        plan["source"],
        {
            "sqlite_snapshot_sha256",
            "sqlite_snapshot_size_bytes",
            "expected_pragma_user_version",
            "expected_table_inventory_sha256",
        },
        label="plan.source",
    )
    for key in ("sqlite_snapshot_sha256", "expected_table_inventory_sha256"):
        if not isinstance(source[key], str) or not _LOWER_SHA_RE.fullmatch(source[key]):
            raise PostgresKernelError("invalid_transform_plan", f"plan.source.{key} must be lowercase SHA-256")
    _nonnegative_int(source["sqlite_snapshot_size_bytes"], label="snapshot size", positive=True)
    _nonnegative_int(source["expected_pragma_user_version"], label="PRAGMA user_version")

    projects = _exact_keys(
        plan["projects"],
        {
            "expected_total",
            "expected_programs",
            "expected_authorized",
            "expected_not_authorized",
        },
        label="plan.projects",
    )
    expected_total = _nonnegative_int(projects["expected_total"], label="project total")
    expected_authorized = _nonnegative_int(projects["expected_authorized"], label="authorized project count")
    expected_not_authorized = _nonnegative_int(
        projects["expected_not_authorized"], label="not-authorized project count"
    )
    expected_programs = _nonnegative_int(projects["expected_programs"], label="program count")
    if expected_authorized + expected_not_authorized + expected_programs != expected_total:
        raise PostgresKernelError("invalid_transform_plan", "Project authorization counts do not sum to expected_total")

    dependencies = _exact_keys(
        plan["project_dependencies"],
        {
            "expected_source_count",
            "affected_gate_from",
            "affected_gate_to",
            "expected_gate_transition_count",
            "retire_dependency_ids",
            "preserve_dependency_ids",
            "expected_active_after",
            "expected_retired_after",
        },
        label="plan.project_dependencies",
    )
    expected_source_count = _nonnegative_int(dependencies["expected_source_count"], label="dependency source count")
    expected_active = _nonnegative_int(dependencies["expected_active_after"], label="active dependency count")
    expected_retired = _nonnegative_int(dependencies["expected_retired_after"], label="retired dependency count")
    expected_gate = _nonnegative_int(
        dependencies["expected_gate_transition_count"], label="dependency gate-transition count"
    )
    if dependencies["affected_gate_from"] != "authorization" or dependencies["affected_gate_to"] != "readiness":
        raise PostgresKernelError(
            "invalid_transform_plan", "Dependency gate transition must be authorization to readiness"
        )
    if expected_active + expected_retired != expected_source_count or expected_gate > expected_source_count:
        raise PostgresKernelError("invalid_transform_plan", "Dependency counts are inconsistent")
    all_dependency_ids: set[str] = set()
    for list_name in ("retire_dependency_ids", "preserve_dependency_ids"):
        values = dependencies[list_name]
        if not isinstance(values, list):
            raise PostgresKernelError("invalid_transform_plan", f"{list_name} must be an array")
        for raw_id in values:
            dependency_id = _opaque_id(raw_id, label=list_name)
            if dependency_id in all_dependency_ids:
                raise PostgresKernelError("invalid_transform_plan", f"Duplicate dependency id: {dependency_id}")
            all_dependency_ids.add(dependency_id)
    if len(all_dependency_ids) != expected_source_count:
        raise PostgresKernelError("invalid_transform_plan", "Dependency lists do not account for expected_source_count")
    return plan


def _identity_key(spec: TableSpec, row: Mapping[str, Any]) -> tuple[bytes, ...]:
    values: list[bytes] = []
    for column in spec.identity_columns:
        value = row.get(column)
        if not isinstance(value, str) or not value:
            raise PostgresKernelError("invalid_manifest", f"{spec.name}.{column} must be non-empty text")
        _validate_text(value, label=f"{spec.name}.{column}")
        values.append(value.encode("utf-8"))
    return tuple(values)


def _canonical_timestamp(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        _validate_text(value, label=label)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PostgresKernelError("invalid_timestamp", f"{label} must be RFC 3339") from exc
    else:
        raise PostgresKernelError("invalid_timestamp", f"{label} must be RFC 3339 text")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PostgresKernelError("invalid_timestamp", f"{label} must include an offset")
    try:
        return parsed.astimezone(UTC).isoformat()
    except (OverflowError, ValueError) as exc:
        raise PostgresKernelError("invalid_timestamp", f"{label} is outside the UTC timestamp range") from exc


def _canonical_date(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        raise PostgresKernelError("invalid_date", f"{label} must be an ISO date")
    if isinstance(value, date):
        parsed = value
    elif isinstance(value, str):
        _validate_text(value, label=label)
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise PostgresKernelError("invalid_date", f"{label} must be an ISO date") from exc
    else:
        raise PostgresKernelError("invalid_date", f"{label} must be an ISO date")
    return parsed.isoformat()


def _require_reference(value: object, allowed: set[str], *, label: str) -> None:
    if value is not None and value not in allowed:
        raise PostgresKernelError("invalid_manifest", f"{label} references a missing current record")


def dependency_shape(record: Mapping[str, Any]) -> bool:
    """The native dependency contract has exact states, never completion aliases."""
    return (
        record.get("dependency_kind") == "requires_project_state"
        and record.get("required_prerequisite_state") in {"active", "verified", "retired", "cancelled"}
        and record.get("affected_gate") in {"readiness", "closure"}
        and isinstance(record.get("rationale"), str)
        and bool(record["rationale"].strip())
    )


def validate_project_dependencies(records: list[dict[str, Any]], projects: dict[str, dict[str, Any]]) -> None:
    """Validate the same current graph for native writes and migration input."""
    edges: dict[str, set[str]] = {}
    semantic: set[tuple[str, str, str]] = set()
    for record in records:
        if record["status"] != "active":
            continue
        if not dependency_shape(record):
            raise PostgresKernelError(
                "invalid_dependency_contract",
                "Reconcile the dependency kind, exact state, gate and rationale",
                details={"id": record["id"]},
            )
        dependent, prerequisite = record["dependent_project_id"], record["prerequisite_project_id"]
        for key in (dependent, prerequisite):
            if key not in projects or projects[key]["kind"] != "project":
                raise PostgresKernelError(
                    "invalid_dependency_endpoint", "Dependencies sequence execution projects", details={"id": key}
                )
        key = (dependent, prerequisite, record["affected_gate"])
        if key in semantic:
            raise PostgresKernelError(
                "duplicate_dependency", "One prerequisite state per project pair and gate is sufficient"
            )
        semantic.add(key)
        edges.setdefault(dependent, set()).add(prerequisite)
        edges.setdefault(prerequisite, set())
    # Closed outcomes remain in the graph; readiness evaluates their state.
    # Iterative cycle detection does not depend on the Python recursion limit.
    pending = {key: set(value) for key, value in edges.items()}
    while pending:
        leaves = {key for key, values in pending.items() if not values}
        if not leaves:
            raise PostgresKernelError(
                "dependency_cycle",
                "The active project dependency graph contains a cycle",
                details={"projects": sorted(pending)},
            )
        pending = {key: values - leaves for key, values in pending.items() if key not in leaves}


def validate_work_item_dependencies(records: list[dict[str, Any]]) -> None:
    """Require the same executable predecessor graph on import and ordinary writes."""
    rows = {row["id"]: row for row in records}
    edges = {}
    for key, row in rows.items():
        dependencies = row.get("depends_on_work_items")
        if dependencies is None:
            dependencies = []
        if not isinstance(dependencies, list) or any(
            not isinstance(value, str)
            or len(value) > 256
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value) is None
            for value in dependencies
        ):
            raise PostgresKernelError(
                "invalid_work_item_dependencies",
                "Work-item predecessors must be identifier strings; "
                "reconcile legacy predicates and non-work references",
                details={"work_item_id": key},
            )
        if len(set(dependencies)) != len(dependencies):
            raise PostgresKernelError(
                "duplicate_work_item_dependency",
                "A work item names each predecessor once",
                details={"work_item_id": key},
            )
        missing = sorted(set(dependencies) - rows.keys())
        if missing:
            raise PostgresKernelError(
                "missing_work_item_dependency",
                "Every predecessor must resolve to a current work item",
                details={"work_item_id": key, "missing_work_item_ids": missing},
            )
        edges[key] = dependencies

    # An iterative DFS reports the actual cycle, not unrelated nodes waiting on it.
    # Closed source labels do not make an invalid current graph safe to migrate.
    complete = set()
    for root in edges:
        if root in complete:
            continue
        path, positions = [root], {root: 0}
        stack = [iter(edges[root])]
        while stack:
            predecessor = next(stack[-1], None)
            if predecessor is None:
                finished = path.pop()
                complete.add(finished)
                positions.pop(finished)
                stack.pop()
            elif predecessor in positions:
                raise PostgresKernelError(
                    "dependency_cycle",
                    "The current work-item predecessor graph contains a cycle",
                    details={"work_item_ids": path[positions[predecessor] :] + [predecessor]},
                )
            elif predecessor not in complete:
                positions[predecessor] = len(path)
                path.append(predecessor)
                stack.append(iter(edges[predecessor]))


def _validate_project_scope_repository(row: Mapping[str, Any]) -> None:
    scope, repository_ref = row.get("application_scope"), row.get("repository_ref")
    if row.get("kind") != "project" or scope is None or repository_ref is None:
        return
    expected = "platform" if scope == "gtkb_platform" else scope
    if repository_ref != expected:
        raise PostgresKernelError(
            "application_scope_mismatch",
            "Explicit project scope and Git storage identity disagree",
            details={"project_id": row["id"], "application_scope": scope, "repository_ref": repository_ref},
        )


def validate_application_scope_relationships(tables: Mapping[str, list[dict[str, Any]]]) -> None:
    """Validate explicit current relationships without classifying historical NULL.

    This is a structural record contract, not caller authentication. Inspection
    and import retain unresolved endpoints; native writers enforce explicit
    scopes separately before admitting a current mutation.
    """
    indexed = {table: {row["id"]: row for row in tables[table] if "id" in row} for table in CURRENT_TABLES}
    for project in tables["projects"]:
        _validate_project_scope_repository(project)

    def reference(table: str, row: dict[str, Any], target_table: str, key: object, *, governing: bool = False) -> None:
        target = indexed[target_table].get(key) if isinstance(key, str) else None
        if target is None:
            return  # Existence remains the typed relationship validator's job.
        scope, target_scope = row.get("application_scope"), target.get("application_scope")
        if scope is None or target_scope is None or scope == target_scope:
            return
        if governing and target_scope == "gtkb_platform":
            return
        raise PostgresKernelError(
            "application_scope_mismatch",
            "Explicit application scopes conflict across a current relationship",
            details={
                "table": table,
                "identity": {column: row[column] for column in TABLE_SPECS[table].identity_columns},
                "application_scope": scope,
                "target_table": target_table,
                "target_id": key,
                "target_application_scope": target_scope,
            },
        )

    scalar = {
        "specifications": (("parent", "specifications", True), ("provisional_until", "specifications", True)),
        "specification_deliberation_sources": (
            ("spec_id", "specifications", False),
            ("deliberation_id", "deliberations", False),
        ),
        "tests": (("spec_id", "specifications", False),),
        "test_plan_phases": (("plan_id", "test_plans", False),),
        "work_items": (("source_spec_id", "specifications", False), ("source_test_id", "tests", False)),
        "projects": (("parent_project_id", "projects", True),),
        "project_work_item_memberships": (("project_id", "projects", False), ("work_item_id", "work_items", False)),
        "project_artifact_links": (("project_id", "projects", False),),
        "testable_elements": (("spec_id", "specifications", False),),
        "deliberations": (("spec_id", "specifications", False), ("work_item_id", "work_items", False)),
        "deliberation_specs": (("deliberation_id", "deliberations", False), ("spec_id", "specifications", False)),
        "deliberation_work_items": (("deliberation_id", "deliberations", False), ("work_item_id", "work_items", False)),
    }
    for table, columns in scalar.items():
        for row in tables[table]:
            for column, target_table, governing in columns:
                reference(table, row, target_table, row.get(column), governing=governing)
    # A historically unscoped relationship row cannot hide a conflict between
    # its two explicitly scoped endpoints. Do not fill the relationship scope.
    pairs = {
        "specification_deliberation_sources": ("specifications", "spec_id", "deliberations", "deliberation_id"),
        "project_work_item_memberships": ("projects", "project_id", "work_items", "work_item_id"),
        "deliberation_specs": ("deliberations", "deliberation_id", "specifications", "spec_id"),
        "deliberation_work_items": ("deliberations", "deliberation_id", "work_items", "work_item_id"),
    }
    for table, (left_table, left_column, right_table, right_column) in pairs.items():
        for row in tables[table]:
            left = indexed[left_table].get(row.get(left_column))
            if left is not None:
                reference(left_table, left, right_table, row.get(right_column))
    arrays = {
        "specifications": (("affected_by", "specifications", True),),
        "test_plan_phases": (("test_ids", "tests", False),),
        "work_items": (
            ("related_deliberation_ids", "deliberations", False),
            ("related_spec_ids_at_creation", "specifications", True),
            ("supersedes", "work_items", False),
            ("superseded_by", "work_items", False),
        ),
    }
    for table, columns in arrays.items():
        for row in tables[table]:
            for column, target_table, governing in columns:
                for key in row.get(column) or ():
                    reference(table, row, target_table, key, governing=governing)
    active_parents: dict[str, list[str]] = {}
    for row in tables["project_work_item_memberships"]:
        if row.get("status") == "active":
            active_parents.setdefault(row["work_item_id"], []).append(row["project_id"])
    for work in tables["work_items"]:
        for key in work.get("depends_on_work_items") or ():
            predecessor = indexed["work_items"].get(key)
            if predecessor is None:
                continue  # The existing dependency graph validator checks existence.
            scope, predecessor_scope = work.get("application_scope"), predecessor.get("application_scope")
            if scope is None or predecessor_scope is None or scope == predecessor_scope:
                continue
            owners, predecessor_owners = active_parents.get(work["id"], []), active_parents.get(key, [])
            matching = [
                edge
                for edge in tables["project_dependencies"]
                if len(owners) == len(predecessor_owners) == 1
                and edge.get("status") == "active"
                and edge.get("dependent_project_id") == owners[0]
                and edge.get("prerequisite_project_id") == predecessor_owners[0]
            ]
            if not matching or any(edge.get("application_scope") != scope for edge in matching):
                raise PostgresKernelError(
                    "application_scope_mismatch",
                    "A foreign work predecessor requires the explicit current project dependency",
                    details={"work_item_id": work["id"], "predecessor_id": key},
                )
            for edge in matching:
                reference("project_dependencies", edge, "projects", edge["dependent_project_id"])
                reference("work_items", predecessor, "projects", edge["prerequisite_project_id"])
    # blocks_work_items is the existing descriptive inverse. The executable
    # predecessor graph is depends_on_work_items; no new edge is invented here.
    for row in tables["project_artifact_links"]:
        if row.get("artifact_type") == "spec":
            reference(
                "project_artifact_links",
                row,
                "specifications",
                row.get("artifact_ref"),
                governing=row.get("relationship") == "governed_by",
            )
            project = indexed["projects"].get(row.get("project_id"))
            if project is not None:
                reference(
                    "projects",
                    project,
                    "specifications",
                    row.get("artifact_ref"),
                    governing=row.get("relationship") == "governed_by",
                )
    for row in tables["test_plan_phases"]:
        plan = indexed["test_plans"].get(row.get("plan_id"))
        if plan is not None:
            for key in row.get("test_ids") or ():
                reference("test_plans", plan, "tests", key)
    for row in tables["work_items"]:
        test = indexed["tests"].get(row.get("source_test_id"))
        if (
            row.get("application_scope") is not None
            and test is not None
            and row.get("source_spec_id") is not None
            and row["source_spec_id"] != test.get("spec_id")
        ):
            raise PostgresKernelError(
                "work_item_source_mismatch",
                "A work item's source test must pair with its exact source specification",
                details={"work_item_id": row["id"], "source_test_id": test["id"]},
            )
    for row in tables["project_dependencies"]:
        reference("project_dependencies", row, "projects", row.get("dependent_project_id"))
        work = indexed["work_items"].get(row.get("related_work_item_id"))
        endpoints = [
            indexed["projects"].get(row.get(column)) for column in ("dependent_project_id", "prerequisite_project_id")
        ]
        if work is not None and work.get("application_scope") is not None:
            scopes = {endpoint.get("application_scope") for endpoint in endpoints if endpoint is not None}
            if None not in scopes and scopes and work["application_scope"] not in scopes:
                raise PostgresKernelError(
                    "application_scope_mismatch",
                    "A dependency's related work item must match either endpoint scope",
                    details={"dependency_id": row["id"], "work_item_id": work["id"]},
                )


def _validate_manifest_relationships(tables: Mapping[str, list[dict[str, Any]]]) -> None:
    ids = {
        table_name: {str(row["id"]) for row in tables[table_name]}
        for table_name in ("specifications", "tests", "test_plans", "work_items", "projects", "deliberations")
    }

    for row in tables["specifications"]:
        _require_reference(row["parent"], ids["specifications"], label="specifications.parent")
        _require_reference(row["provisional_until"], ids["specifications"], label="specifications.provisional_until")
    for row in tables["specification_deliberation_sources"]:
        _require_reference(row["spec_id"], ids["specifications"], label="source.spec_id")
        _require_reference(row["deliberation_id"], ids["deliberations"], label="source.deliberation_id")
    for row in tables["tests"]:
        _require_reference(row["spec_id"], ids["specifications"], label="tests.spec_id")
    for row in tables["test_plan_phases"]:
        _require_reference(row["plan_id"], ids["test_plans"], label="test_plan_phases.plan_id")
    for row in tables["work_items"]:
        _require_reference(row["source_spec_id"], ids["specifications"], label="work_items.source_spec_id")
        _require_reference(row["source_test_id"], ids["tests"], label="work_items.source_test_id")
    validate_work_item_dependencies(tables["work_items"])
    projects = {row["id"]: row for row in tables["projects"]}
    for row in tables["projects"]:
        _require_reference(row["parent_project_id"], ids["projects"], label="projects.parent_project_id")
        parent_id = row["parent_project_id"]
        if parent_id is not None and (row["kind"] != "project" or projects[parent_id]["kind"] != "program"):
            raise PostgresKernelError("invalid_manifest", "Only a program can contain execution projects")

    membership_pairs: set[tuple[str, str]] = set()
    active_parent_counts: dict[str, int] = {}
    for row in tables["project_work_item_memberships"]:
        _require_reference(row["project_id"], ids["projects"], label="membership.project_id")
        _require_reference(row["work_item_id"], ids["work_items"], label="membership.work_item_id")
        pair = (row["project_id"], row["work_item_id"])
        if pair in membership_pairs:
            raise PostgresKernelError("invalid_manifest", "Duplicate current project/work-item relationship")
        membership_pairs.add(pair)
        if row["status"] == "active":
            if projects[row["project_id"]]["kind"] != "project":
                raise PostgresKernelError("invalid_manifest", "A program cannot contain work items")
            active_parent_counts[row["work_item_id"]] = active_parent_counts.get(row["work_item_id"], 0) + 1

    # Open work is constituted under exactly one execution project; intake and
    # every native membership write keep that rule. Closed work migrates with
    # its recorded membership history exactly: zero or several active rows are
    # historical facts that are neither repaired nor fabricated here.
    open_items = {str(row["id"]) for row in tables["work_items"] if row.get("resolution_status") == "open"}
    multiple_parents = sorted(item for item in open_items if active_parent_counts.get(item, 0) > 1)
    if multiple_parents:
        raise PostgresKernelError(
            "invalid_manifest",
            "An open work item cannot have multiple active parent projects",
            details={"work_item_ids": multiple_parents[:20]},
        )
    missing_parents = sorted(item for item in open_items if active_parent_counts.get(item, 0) == 0)
    if missing_parents:
        raise PostgresKernelError(
            "invalid_manifest",
            "Every open work item requires one active parent project",
            details={"missing_parent_count": len(missing_parents), "work_item_ids": missing_parents[:20]},
        )

    for row in tables["project_dependencies"]:
        _require_reference(row["dependent_project_id"], ids["projects"], label="dependency.dependent_project_id")
        _require_reference(
            row["prerequisite_project_id"],
            ids["projects"],
            label="dependency.prerequisite_project_id",
        )
        _require_reference(row["related_work_item_id"], ids["work_items"], label="dependency.related_work_item_id")
    validate_project_dependencies(tables["project_dependencies"], projects)
    for row in tables["project_artifact_links"]:
        _require_reference(row["project_id"], ids["projects"], label="artifact_link.project_id")
    for row in tables["testable_elements"]:
        _require_reference(row["spec_id"], ids["specifications"], label="testable_element.spec_id")
    for row in tables["deliberations"]:
        _require_reference(row["spec_id"], ids["specifications"], label="deliberation.spec_id")
        _require_reference(row["work_item_id"], ids["work_items"], label="deliberation.work_item_id")
    for row in tables["deliberation_specs"]:
        _require_reference(row["deliberation_id"], ids["deliberations"], label="deliberation_specs.deliberation_id")
        _require_reference(row["spec_id"], ids["specifications"], label="deliberation_specs.spec_id")
    for row in tables["deliberation_work_items"]:
        _require_reference(
            row["deliberation_id"],
            ids["deliberations"],
            label="deliberation_work_items.deliberation_id",
        )
        _require_reference(row["work_item_id"], ids["work_items"], label="deliberation_work_items.work_item_id")

    environment_keys: set[tuple[str, str, str]] = set()
    for row in tables["environment_config"]:
        key = (row["environment"], row["category"], row["key"])
        if key in environment_keys:
            raise PostgresKernelError("invalid_manifest", "Duplicate current environment configuration key")
        environment_keys.add(key)
    validate_application_scope_relationships(tables)


def _normalize_manifest_row(
    table_name: str,
    raw_row: object,
    *,
    require_version_one: bool,
) -> dict[str, Any]:
    spec = TABLE_SPECS[table_name]
    if table_name == "projects" and isinstance(raw_row, dict):
        raw_row = {"planning_estimate": None, **raw_row}
    if table_name in _NEW_APPLICATION_SCOPE_TABLES and isinstance(raw_row, dict):
        # The exact prior row shape had no scope column. Only that additive
        # omission is accepted; absent old fields and unknown fields still fail.
        raw_row = {"application_scope": None, **raw_row}
    row = dict(_exact_keys(raw_row, set(spec.columns), label=f"manifest row {table_name}"))
    _identity_key(spec, row)
    version = row.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PostgresKernelError("invalid_manifest", f"Invalid version in {table_name}")
    if version > PG_INTEGER_MAX:
        raise PostgresKernelError("invalid_manifest", f"Version is outside PostgreSQL range in {table_name}")
    if require_version_one and version != 1:
        raise PostgresKernelError("invalid_manifest", f"Imported version must restart at 1 in {table_name}")
    for timestamp, calendar_date in _DATE_PRECISION_FIELDS.get(table_name, ()):
        if row[timestamp] is not None and row[calendar_date] is not None:
            raise PostgresKernelError(
                "invalid_manifest", f"{table_name} must supply either {timestamp} or {calendar_date}, not both"
            )
    if table_name in APPLICATION_SCOPE_TABLES and row["application_scope"] is not None:
        scope = row["application_scope"]
        if (
            not isinstance(scope, str)
            or re.fullmatch(r"gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*", scope) is None
        ):
            raise PostgresKernelError("invalid_manifest", f"Invalid {table_name}.application_scope")
    if table_name == "work_items":
        # Predecessor shape and graph diagnostics stay with their domain validator.
        for column in spec.json_columns - {"depends_on_work_items"}:
            value = row[column]
            if value is not None and (
                not isinstance(value, list)
                or any(not isinstance(reference, str) or not reference.strip() for reference in value)
            ):
                raise PostgresKernelError(
                    "invalid_manifest",
                    f"work_items.{column} must be an array of nonempty references or null",
                )
    if table_name == "projects":
        if row.get("kind") not in {"program", "project"}:
            raise PostgresKernelError("invalid_manifest", "Invalid projects.kind")
        repository_ref = row.get("repository_ref")
        if repository_ref is not None and (
            not isinstance(repository_ref, str)
            or re.fullmatch(r"platform|application:[A-Za-z][A-Za-z0-9_-]*", repository_ref) is None
        ):
            raise PostgresKernelError("invalid_manifest", "Invalid projects.repository_ref")
        if row["kind"] == "program" and repository_ref is not None:
            raise PostgresKernelError("invalid_manifest", "Programs have no repository association")
        if row["kind"] == "program" and row.get("authorization") is not None:
            raise PostgresKernelError("invalid_manifest", "Programs have no authorization")
        if row["kind"] == "project" and row.get("authorization") not in {"authorized", "not authorized"}:
            raise PostgresKernelError("invalid_manifest", "Invalid projects.authorization")
        if row["id"] == "PROJECT-GTKB-NEW-WORK-INTAKE" and row.get("authorization") != "not authorized":
            raise PostgresKernelError("invalid_manifest", "Standing intake must remain not authorized")
        _validate_project_scope_repository(row)
        try:
            row["planning_estimate"] = validate_estimate(row["planning_estimate"])
        except PlanningError as error:
            raise PostgresKernelError("invalid_manifest", "Invalid projects.planning_estimate") from error
    if table_name == "project_dependencies" and row.get("affected_gate") == "authorization":
        raise PostgresKernelError("invalid_manifest", "Project dependency affected_gate was not transformed")
    if table_name == "canonical_terms":
        if row.get("authority_level") not in {"platform_core", "adopter_extension", "project_local"}:
            raise PostgresKernelError("invalid_manifest", "Invalid canonical_terms.authority_level")
        if row.get("lifecycle_status") not in {"candidate", "active", "deprecated", "retired"}:
            raise PostgresKernelError("invalid_manifest", "Invalid canonical_terms.lifecycle_status")
    for column in spec.columns:
        value = row[column]
        if column in _REQUIRED_COLUMNS[table_name] and value is None:
            raise PostgresKernelError("invalid_manifest", f"{table_name}.{column} must not be null")
        if column in _TIMESTAMP_COLUMNS:
            row[column] = _canonical_timestamp(value, label=f"{table_name}.{column}")
        elif column in _DATE_COLUMNS:
            row[column] = _canonical_date(value, label=f"{table_name}.{column}")
        elif value is None or column in spec.json_columns:
            continue
        elif column in spec.boolean_columns:
            if type(value) is not bool:
                raise PostgresKernelError("invalid_manifest", f"{table_name}.{column} must be boolean")
        elif column in _INTEGER_COLUMNS:
            if type(value) is not int or not PG_INTEGER_MIN <= value <= PG_INTEGER_MAX:
                raise PostgresKernelError(
                    "invalid_manifest",
                    f"{table_name}.{column} must fit a PostgreSQL integer",
                )
        elif not isinstance(value, str):
            raise PostgresKernelError("invalid_manifest", f"{table_name}.{column} must be text or null")
    canonical_json_bytes(row)
    if table_name == "tests" and row["execution_evidence"] is not None:
        from pydantic import ValidationError

        try:
            from ._evidence import portfolio_result_identity, qualification_entries

            entries = qualification_entries(row["execution_evidence"])
            evidence = entries[0]
            result_identity = portfolio_result_identity(row["execution_evidence"])
            if row["execution_evidence"].get("format") == "gtkb.native.qualification.portfolio.v1" and any(
                (item.test_id, item.test_version) != (row["id"], row["version"]) for item in entries
            ):
                raise ValueError("Every native portfolio cell belongs to this atomic TEST version")
        except (ValidationError, ValueError) as error:
            raise PostgresKernelError(
                "invalid_manifest", "Native test execution evidence has an invalid shape"
            ) from error
        if (
            evidence.test_id != row["id"]
            or evidence.test_version > row["version"]
            or evidence.selector.model_dump() != {key: row[key] for key in ("test_file", "test_class", "test_function")}
            or row["last_result"] not in {"pass", "fail"}
            or row["last_executed_at"] is None
            or result_identity[2] != row["last_executed_at"]
        ):
            raise PostgresKernelError("invalid_manifest", "Native test evidence differs from its executed definition")
    return row


def normalize_bindings(value: object) -> list[dict[str, Any]]:
    """Preserve checked enrollment versus attribution without inventing authority."""
    from pydantic import ValidationError

    from .record_types import BindingCarrier

    if not isinstance(value, list):
        raise PostgresKernelError("invalid_manifest", "Session bindings must be an array")
    result = []
    native_ids, context_ids = set(), set()
    for raw in value:
        row = dict(_exact_keys(raw, set(BINDING_COLUMNS), label="session_init_bindings"))
        for column in BINDING_COLUMNS:
            if column not in {"created_at", "execution_observation", "subject"} and row[column] is not None:
                _opaque_id(row[column], label=f"session_init_bindings.{column}")
        row["created_at"] = _canonical_timestamp(row["created_at"], label="session_init_bindings.created_at")
        if row["created_at"] is None:
            raise PostgresKernelError("invalid_manifest", "An immutable binding requires its actual creation time")
        canonical_json_bytes(row)
        try:
            BindingCarrier.model_validate(row)
        except ValidationError as error:
            raise PostgresKernelError(
                "invalid_manifest", "Invalid execution enrollment or initialized binding"
            ) from error
        if row["native_context_id"] in native_ids or (
            row["session_context_id"] is not None and row["session_context_id"] in context_ids
        ):
            raise PostgresKernelError("invalid_manifest", "Session binding identities must be unique")
        native_ids.add(row["native_context_id"])
        if row["session_context_id"] is not None:
            context_ids.add(row["session_context_id"])
        result.append(row)
    return sorted(result, key=lambda row: row["native_context_id"].encode("utf-8"))


def normalize_manifest(value: object, *, require_version_one: bool = True) -> dict[str, Any]:
    manifest = _exact_keys(value, {"format", "schema_version", "tables"}, label="manifest")
    if (
        manifest["format"] != CURRENT_FORMAT
        or type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != SCHEMA_VERSION
    ):
        raise PostgresKernelError("invalid_manifest", "Unsupported migration-manifest format or schema version")
    tables = _exact_keys(manifest["tables"], set(MIGRATION_TABLES), label="manifest.tables")
    normalized_tables: dict[str, list[dict[str, Any]]] = {}
    for table_name in CURRENT_TABLES:
        rows = tables[table_name]
        if not isinstance(rows, list):
            raise PostgresKernelError("invalid_manifest", f"Manifest table {table_name} must be an array")
        spec = TABLE_SPECS[table_name]
        seen: set[tuple[bytes, ...]] = set()
        normalized_rows: list[dict[str, Any]] = []
        for raw_row in rows:
            row = _normalize_manifest_row(
                table_name,
                raw_row,
                require_version_one=require_version_one,
            )
            identity = _identity_key(spec, row)
            if identity in seen:
                raise PostgresKernelError("invalid_manifest", f"Duplicate manifest identity in {table_name}")
            seen.add(identity)
            normalized_rows.append(row)
        normalized_rows.sort(key=lambda row: _identity_key(spec, row))
        normalized_tables[table_name] = normalized_rows
    normalized_tables["session_init_bindings"] = normalize_bindings(tables["session_init_bindings"])
    _validate_manifest_relationships(normalized_tables)
    return {"format": CURRENT_FORMAT, "schema_version": SCHEMA_VERSION, "tables": normalized_tables}


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _publish_bytes(path: Path, payload: bytes) -> str:
    def existing_status() -> str:
        if path.is_file() and not path.is_symlink() and path.read_bytes() == payload:
            return "already_current"
        raise PostgresKernelError("output_exists", "Output exists with different content")

    try:
        if path.exists():
            return existing_status()
        if not path.parent.is_dir():
            raise PostgresKernelError("output_parent_missing", "Output parent directory does not exist")
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        publication_status: str | None = None
        publication_error: PostgresKernelError | OSError | None = None
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
                publication_status = "ok"
            except FileExistsError:
                publication_status = existing_status()
        except (PostgresKernelError, OSError) as exc:
            publication_error = exc
        try:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    os.unlink(temporary)
        except OSError as cleanup_error:
            if publication_error is None and publication_status is None:
                publication_error = cleanup_error
        if publication_error is not None:
            raise publication_error
        if publication_status is None:
            raise OSError("Canonical output publication produced no result")
        return publication_status
    except PostgresKernelError:
        raise
    except OSError as exc:
        raise PostgresKernelError("output_write_failed", "Canonical output could not be published") from exc


def _json_from_sqlite(value: object, *, table: str, column: str) -> object:
    if value is None or isinstance(value, (dict, list, bool, int, float)):
        canonical_json_bytes(value)
        return value
    if not isinstance(value, str):
        raise PostgresKernelError("invalid_source", f"{table}.{column} is not JSON text")
    try:
        parsed = parse_json_bytes(value.encode("utf-8"))
    except PostgresKernelError as exc:
        raise PostgresKernelError("invalid_source", f"{table}.{column} contains invalid JSON") from exc
    return parsed


def _sqlite_inventory(connection: sqlite3.Connection) -> dict[str, Any]:
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM main.sqlite_schema WHERE type='table' AND substr(name,1,7) <> 'sqlite_' ORDER BY name"
        ).fetchall()
    ]
    table_list_all = connection.execute(
        "SELECT schema,name,type,ncol,wr,strict FROM pragma_table_list WHERE schema='main'"
    ).fetchall()
    table_list_by_name: dict[str, list[sqlite3.Row]] = {}
    for row in table_list_all:
        table_list_by_name.setdefault(str(row["name"]), []).append(row)
    table_rows: list[dict[str, Any]] = []
    column_rows: dict[str, list[dict[str, Any]]] = {}
    for name in names:
        matches = table_list_by_name.get(str(name), [])
        if len(matches) != 1:
            raise PostgresKernelError("invalid_sqlite_inventory", f"Expected one table_list row for: {name}")
        table_rows.append(dict(matches[0]))
        rows = connection.execute(
            'SELECT cid,name,type,"notnull",dflt_value,pk,hidden FROM pragma_table_xinfo(?)',
            (name,),
        ).fetchall()
        column_rows[str(name)] = [dict(row) for row in rows]
    return normalize_table_inventory(table_rows, column_rows)


def _current_sqlite_rows(connection: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    quoted = sql.Identifier(table_name)
    columns = {row["name"] for row in connection.execute(sql.SQL("PRAGMA table_info({})").format(quoted).as_string())}
    if "version" not in columns:
        return [dict(row) for row in connection.execute(sql.SQL("SELECT * FROM {}").format(quoted).as_string())]
    spec = TABLE_SPECS[table_name]
    keys = sql.SQL(",").join(sql.Identifier(key) for key in spec.identity_columns)
    invalid = connection.execute(
        sql.SQL("SELECT 1 FROM {} WHERE typeof(version) <> 'integer' OR version < 1 LIMIT 1").format(quoted).as_string()
    ).fetchone()
    if invalid is not None:
        raise PostgresKernelError("invalid_source", f"Invalid source version in {table_name}")
    duplicate = connection.execute(
        sql.SQL("SELECT 1 FROM {} GROUP BY {},version HAVING COUNT(*) > 1 LIMIT 1").format(quoted, keys).as_string()
    ).fetchone()
    if duplicate is not None:
        raise PostgresKernelError("invalid_source", f"Duplicate source identity/version in {table_name}")
    # Select current payloads in SQLite. Historical bodies can dominate the
    # source size and are not migration input; never materialize them in Python.
    rows = [
        dict(row)
        for row in connection.execute(
            sql.SQL(
                "SELECT t.* FROM {table} t JOIN "
                "(SELECT {keys},MAX(version) version FROM {table} GROUP BY {keys}) h USING ({keys},version)"
            )
            .format(table=quoted, keys=keys)
            .as_string()
        )
    ]
    for row in rows:
        _identity_key(spec, row)
    return rows


def _transform_source_rows(
    source_rows: Mapping[str, list[dict[str, Any]]], plan: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    current_spec_versions = {str(row["id"]): int(row["version"]) for row in source_rows["specifications"]}
    dependency_plan = plan["project_dependencies"]
    retire_ids = set(dependency_plan["retire_dependency_ids"])
    preserve_ids = set(dependency_plan["preserve_dependency_ids"])
    source_dependency_ids = {str(row["id"]) for row in source_rows["project_dependencies"]}
    if source_dependency_ids != retire_ids | preserve_ids:
        raise PostgresKernelError(
            "transform_precondition_failed", "Dependency plan does not exactly account for source IDs"
        )
    dependency_rows_by_id = {str(row["id"]): row for row in source_rows["project_dependencies"]}
    if any(dependency_rows_by_id[dependency_id].get("status") != "active" for dependency_id in retire_ids):
        raise PostgresKernelError(
            "transform_precondition_failed",
            "Every planned dependency retirement must name a current active row",
        )

    gate_transition_count = 0
    for table_name in CURRENT_TABLES:
        spec = TABLE_SPECS[table_name]
        source_table_rows = source_rows[table_name]
        if table_name == "project_work_item_memberships":
            # Migration restarts current state. Inactive links describe former
            # associations, preserved in the source/backup, not extra parents.
            historical_statuses = {"removed", "retired", "moved", "superseded", "completed", "excluded", "rehomed"}
            for source_row in source_table_rows:
                if source_row.get("status") not in historical_statuses | {"active"}:
                    raise PostgresKernelError(
                        "invalid_source",
                        "Unknown project membership status",
                        details={"membership_id": source_row.get("id"), "status": source_row.get("status")},
                    )
            source_table_rows = [row for row in source_table_rows if row["status"] == "active"]
        if table_name == "specification_deliberation_sources":
            for source_row in source_table_rows:
                source_spec_id = str(source_row.get("spec_id"))
                source_spec_version = source_row.get("spec_version")
                if source_spec_id not in current_spec_versions:
                    raise PostgresKernelError(
                        "transform_precondition_failed",
                        "Specification source link names an unknown current specification",
                    )
                if (
                    not isinstance(source_spec_version, int)
                    or isinstance(source_spec_version, bool)
                    or source_spec_version < 1
                ):
                    raise PostgresKernelError(
                        "transform_precondition_failed",
                        "Specification source link has an invalid source version",
                    )
            source_table_rows = [
                row
                for row in source_table_rows
                if current_spec_versions[str(row.get("spec_id"))] == row.get("spec_version")
            ]
        transformed: list[dict[str, Any]] = []
        seen: set[tuple[bytes, ...]] = set()
        for source in source_table_rows:
            row: dict[str, Any] = {column: source.get(column) for column in spec.columns}
            row["version"] = 1
            for timestamp, calendar_date in _DATE_PRECISION_FIELDS.get(table_name, ()):
                value = row[timestamp]
                if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    if row[calendar_date] is not None:
                        raise PostgresKernelError(
                            "invalid_source", f"Ambiguous date precision in {table_name}.{timestamp}"
                        )
                    row[calendar_date] = _canonical_date(value, label=f"{table_name}.{calendar_date}")
                    row[timestamp] = None
            for column in spec.json_columns:
                row[column] = _json_from_sqlite(row[column], table=table_name, column=column)
            for column in spec.boolean_columns:
                value = row[column]
                if type(value) is not int or value not in (0, 1):
                    raise PostgresKernelError("invalid_source", f"{table_name}.{column} is not boolean")
                row[column] = bool(value)
            for column in spec.columns:
                if column in _TIMESTAMP_COLUMNS:
                    row[column] = _canonical_timestamp(row[column], label=f"{table_name}.{column}")
                elif column in _DATE_COLUMNS:
                    row[column] = _canonical_date(row[column], label=f"{table_name}.{column}")
            if table_name == "project_dependencies":
                if source.get("affected_gate") == dependency_plan["affected_gate_from"]:
                    row["affected_gate"] = dependency_plan["affected_gate_to"]
                    gate_transition_count += 1
                if str(row["id"]) in retire_ids:
                    row["status"] = "retired"
            identity = _identity_key(spec, row)
            if identity in seen:
                raise PostgresKernelError(
                    "transform_precondition_failed", f"Duplicate current relationship in {table_name}"
                )
            seen.add(identity)
            transformed.append(row)
        transformed.sort(key=lambda row: _identity_key(spec, row))
        output[table_name] = transformed

    projects = output["projects"]
    authorized_count = sum(row["authorization"] == "authorized" for row in projects)
    not_authorized_count = sum(row["authorization"] == "not authorized" for row in projects)
    program_count = sum(row["kind"] == "program" for row in projects)
    project_plan = plan["projects"]
    if (
        len(projects) != project_plan["expected_total"]
        or authorized_count != project_plan["expected_authorized"]
        or not_authorized_count != project_plan["expected_not_authorized"]
        or program_count != project_plan["expected_programs"]
    ):
        raise PostgresKernelError("transform_precondition_failed", "Project count or authorization result drifted")

    dependencies = output["project_dependencies"]
    active_count = sum(row["status"] == "active" for row in dependencies)
    retired_count = sum(row["status"] == "retired" for row in dependencies)
    if (
        len(dependencies) != dependency_plan["expected_source_count"]
        or gate_transition_count != dependency_plan["expected_gate_transition_count"]
        or active_count != dependency_plan["expected_active_after"]
        or retired_count != dependency_plan["expected_retired_after"]
    ):
        raise PostgresKernelError("transform_precondition_failed", "Dependency transform counts drifted")
    # Only the explicit legacy SQLite transformation fills the nullable
    # execution enrollment column. Binding manifests remain exact and cannot
    # silently discard a current execution enrollment.
    if any(row.get("execution_observation") is not None for row in source_rows["session_init_bindings"]):
        raise PostgresKernelError(
            "invalid_source", "The legacy SQLite transform cannot discard an execution enrollment"
        )
    output["session_init_bindings"] = normalize_bindings(
        [{**row, "execution_observation": None} for row in source_rows["session_init_bindings"]]
    )
    return output


def _normalize_pg_value(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise PostgresKernelError("invalid_timestamp", "PostgreSQL returned a timestamp without an offset")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_normalize_pg_value(item) for item in value]
    if isinstance(value, list):
        return [_normalize_pg_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_pg_value(item) for key, item in value.items()}
    return value


def _select_columns(spec: TableSpec) -> Any:
    """Select JSONB as text so psycopg cannot round precise numbers through float."""
    expressions: list[Any] = []
    for column in spec.columns:
        if column in spec.json_columns:
            expressions.append(sql.SQL("{}::text AS {}").format(sql.Identifier(column), sql.Identifier(column)))
        else:
            expressions.append(sql.Identifier(column))
    return sql.SQL(",").join(expressions)


def _decode_pg_json(value: object, *, label: str) -> object:
    if isinstance(value, str):
        raw = value.encode("utf-8")
    elif isinstance(value, bytes):
        raw = value
    else:
        raise PostgresKernelError(
            "postgres_readback_invalid",
            f"PostgreSQL returned non-text JSON for {label}",
        )
    try:
        return parse_json_bytes(raw)
    except PostgresKernelError as exc:
        raise PostgresKernelError(
            "postgres_readback_invalid",
            f"PostgreSQL returned invalid JSON for {label}",
        ) from exc


def _normalize_pg_row(raw: Mapping[str, object], spec: TableSpec) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for column in spec.columns:
        if column not in raw:
            raise PostgresKernelError(
                "postgres_readback_invalid",
                f"PostgreSQL omitted {spec.name}.{column}",
            )
        value = raw[column]
        if column in spec.json_columns and value is not None:
            row[column] = _decode_pg_json(value, label=f"{spec.name}.{column}")
        else:
            row[column] = _normalize_pg_value(value)
    return row


@dataclass(eq=False, repr=False)
class _UnresolvedPostgresConnect:
    pgconn: Any
    primary: BaseException
    cleanup_error: BaseException
    owners: _ConnectCollections


_ConnectCollections = tuple[set[_UnresolvedPostgresConnect], ...]
_ConnectCheckpoint = Callable[[], None]


def _connect_collections(values: _ConnectCollections, *, required: bool = True) -> _ConnectCollections:
    if type(values) is not tuple or any(type(value) is not set for value in values):
        raise TypeError("Use actual owner-local PostgreSQL cleanup collections")
    unique: list[set[_UnresolvedPostgresConnect]] = []
    for value in values:
        if not any(value is item for item in unique):
            unique.append(value)
    if required and not unique:
        raise ValueError("A PostgreSQL connection requires its original cleanup owner")
    return tuple(unique)


def _connect_checkpoint(deadline: Deadline, callback: _ConnectCheckpoint | None) -> None:
    if callback is not None:
        callback()
    if expired(deadline):
        raise PostgresKernelError("postgres_connection_deadline_expired", "The original PostgreSQL operation expired")


def _sql_timeouts(policy: Any, deadline: Deadline) -> tuple[int, int]:
    """Narrow captured server limits to the remaining original parent."""
    left = remaining(deadline)
    if left is None:
        return policy.lock_timeout_ms, policy.statement_timeout_ms
    # PostgreSQL uses whole milliseconds; zero disables a timeout. Refuse a
    # sub-millisecond remainder instead of converting a finite bound to zero.
    milliseconds = 2147483647 if left >= 2147483.647 else int(left * 1000)
    if milliseconds < 1:
        raise PostgresKernelError('postgres_connection_deadline_expired',
                                  'The original PostgreSQL operation has no whole millisecond remaining')
    return tuple(min(value, milliseconds) if value else milliseconds
                 for value in (policy.lock_timeout_ms, policy.statement_timeout_ms))


class _DeadlineCursor:
    """Refresh server statement bounds before each internal domain statement.

    Checkpoints are cooperative, between blocking driver calls. Server timeouts
    do not promise interruption of network I/O or driver's transaction controls.
    """
    def __init__(self, original: Any, policy: Any, deadline: Deadline,
                 checkpoint: _ConnectCheckpoint | None) -> None:
        self._original, self._policy = original, policy
        self._deadline, self._checkpoint = deadline, checkpoint

    @property
    def rowcount(self) -> int:
        return self._original.rowcount

    def configure(self) -> None:
        _connect_checkpoint(self._deadline, self._checkpoint)
        if self._deadline is None:
            return
        lock, statement = _sql_timeouts(self._policy, self._deadline)
        self._original.execute(
            "SELECT set_config('statement_timeout', %s, true), set_config('lock_timeout', %s, true)",
            (f'{statement}ms', f'{lock}ms'),
        )
        _connect_checkpoint(self._deadline, self._checkpoint)

    def execute(self, *args: Any, **kwargs: Any) -> _DeadlineCursor:
        self.configure()
        self._original.execute(*args, **kwargs)
        _connect_checkpoint(self._deadline, self._checkpoint)
        return self

    def fetchone(self) -> Any:
        _connect_checkpoint(self._deadline, self._checkpoint)
        result = self._original.fetchone()
        _connect_checkpoint(self._deadline, self._checkpoint)
        return result

    def fetchall(self) -> Any:
        _connect_checkpoint(self._deadline, self._checkpoint)
        result = self._original.fetchall()
        _connect_checkpoint(self._deadline, self._checkpoint)
        return result


def connect_postgresql(
    *,
    unresolved_connects: _ConnectCollections,
    autocommit: bool = False,
    row_factory: Any = dict_row,
    deadline: Deadline = None,
    checkpoint: _ConnectCheckpoint | None = None,
    poll_interval: float,
    **parameters: str | int,
) -> psycopg.Connection[Any]:
    """Own the explicit loopback original through polling, setup and ending.

    The exact3.3.4 Connection constructor is a source-backed compatibility
    boundary. No generator or address-attempt helper owns a hidden original.
    """
    owners = _connect_collections(unresolved_connects)
    deadline = checked(deadline)
    _connect_checkpoint(deadline, checkpoint)
    if {"context", "cursor_factory", "prepare_threshold"}.intersection(parameters):
        raise TypeError("Unsupported PostgreSQL Python connection option")
    if parameters.get("host") != "127.0.0.1" or parameters.get("hostaddr") != "127.0.0.1":
        raise PostgresKernelError("invalid_postgresql_config", "Use the explicit loopback PostgreSQL binding")
    if "connect_timeout" in parameters and (
        isinstance(parameters["connect_timeout"], bool) or parameters["connect_timeout"] not in (0, "0")
    ):
        raise ValueError("Use the original operation parent, not an independent PostgreSQL connect timeout")
    conninfo = make_conninfo(**{**parameters, "connect_timeout": 0})
    _connect_checkpoint(deadline, checkpoint)
    # No returned object exists before connect_start returns. Once returned,
    # capture custody before every later checkpoint, poll or wrapper operation.
    pgconn = psycopg.pq.PGconn.connect_start(conninfo.encode("utf-8"))
    try:
        _connect_checkpoint(deadline, checkpoint)
        if pgconn.status == psycopg.pq.ConnStatus.BAD:
            raise psycopg.OperationalError("PostgreSQL connection start failed")
        direction = psycopg.pq.PollingStatus.WRITING
        while True:
            _connect_checkpoint(deadline, checkpoint)
            socket = pgconn.socket
            if socket < 0:
                raise psycopg.OperationalError("PostgreSQL connection socket is unavailable")
            parent_left = remaining(deadline)
            interval = poll_interval if parent_left is None else min(poll_interval, parent_left)
            reads = [socket] if direction == psycopg.pq.PollingStatus.READING else []
            writes = [socket] if direction == psycopg.pq.PollingStatus.WRITING else []
            ready_read, ready_write, exceptional = select.select(reads, writes, [socket], interval)
            _connect_checkpoint(deadline, checkpoint)
            if not ready_read and not ready_write and not exceptional:
                continue
            direction = pgconn.connect_poll()
            _connect_checkpoint(deadline, checkpoint)
            if direction == psycopg.pq.PollingStatus.OK:
                if pgconn.status != psycopg.pq.ConnStatus.OK:
                    raise psycopg.OperationalError("PostgreSQL connection completion is unavailable")
                break
            if direction not in (psycopg.pq.PollingStatus.READING, psycopg.pq.PollingStatus.WRITING):
                raise psycopg.OperationalError("PostgreSQL connection polling failed")
            # Reacquire pgconn.socket on every next iteration: libpq may change it.
        pgconn.nonblocking = 1
        connection = psycopg.Connection(pgconn, row_factory=row_factory)
        connection.autocommit = autocommit
        _connect_checkpoint(deadline, checkpoint)
        return connection
    except BaseException as primary:
        try:
            pgconn.finish()
            if pgconn.status != psycopg.pq.ConnStatus.BAD:
                raise PostgresKernelError("authority_ending_unresolved", "Original PostgreSQL finish is unconfirmed")
        except BaseException as cleanup:
            record = _UnresolvedPostgresConnect(pgconn, primary, cleanup, owners)
            for owner in owners:
                owner.add(record)
            if not isinstance(cleanup, Exception):
                raise
            primary.add_note("AUTH connection cleanup failed (" + type(cleanup).__name__ + ")")
        # Bare raise preserves the primary and its existing cause.
        raise



@dataclass(eq=False, repr=False)
class _PostgresConnectionScope:
    deadline: Deadline
    checkpoint: _ConnectCheckpoint | None
    unresolved_connects: _ConnectCollections
    consumer: threading.Thread
    active: bool = True


class PostgresKernel(OperationOwner):
    """PostgreSQL kernel service with injectable connectors for database-free tests."""

    def __init__(self, settings: PostgresSettings, *, project_root: Path,
                 parameters_provider: ParametersProvider, operation_provider: OperationProvider) -> None:
        self._initialize_owner(settings, project_root, parameters_provider, operation_provider)
        self._sqlite_connector = None
        self._connection_local = threading.local()
        self._connect_cleanup: set[_UnresolvedPostgresConnect] = set()

    @property
    def project_root(self) -> Path | None:
        """The selected installation root; never infer it from a caller's cwd."""
        return self._project_root

    @contextmanager
    def connection_scope(
        self,
        *,
        deadline: Deadline,
        checkpoint: _ConnectCheckpoint | None = None,
        unresolved_connects: _ConnectCollections = (),
    ) -> Iterator[None]:
        with self._owner_lock:
            if getattr(self._operation_local, 'frame', None) is None:
                self._check_admission()
        _connect_checkpoint(checked(deadline), checkpoint)
        with self.operation_scope():
            previous = getattr(self._connection_local, "frames", ())
            if any(not frame.active for frame in previous):
                raise PostgresKernelError("postgres_connection_scope_changed", "Use the original connection scope")
            frame = _PostgresConnectionScope(
                checked(deadline),
                checkpoint,
                _connect_collections(unresolved_connects, required=False),
                threading.current_thread(),
            )
            current = (*previous, frame)
            self._connection_local.frames = current
            primary = None
            try:
                yield
            except BaseException as error:
                primary = error
                raise
            finally:
                frame.active = False
                if (
                    threading.current_thread() is not frame.consumer
                    or getattr(self._connection_local, "frames", ()) is not current
                ):
                    failure = PostgresKernelError("postgres_connection_scope_changed", "Use the original connection scope")
                    if primary is not None:
                        primary.add_note("AUTH connection scope cleanup failed (" + type(failure).__name__ + ")")
                        raise primary from primary.__cause__
                    raise failure
                self._connection_local.frames = previous

    def _connection_inputs(
        self, deadline: Deadline, checkpoint: _ConnectCheckpoint | None
    ) -> tuple[Deadline, _ConnectCheckpoint | None, _ConnectCollections]:
        frames = getattr(self._connection_local, "frames", ())
        if any(not frame.active or frame.consumer is not threading.current_thread() for frame in frames):
            raise PostgresKernelError("postgres_connection_scope_changed", "Use the original connection scope")
        policy = self._operation_policy()
        effective = earliest(policy.deadline, checked(deadline), *(frame.deadline for frame in frames))
        callbacks = (policy.checkpoint, *(frame.checkpoint for frame in frames if frame.checkpoint is not None))
        if checkpoint is not None:
            callbacks = (*callbacks, checkpoint)

        def original_checks() -> None:
            for callback in callbacks:
                callback()

        owners = _connect_collections(
            (self._connect_cleanup, *(owner for frame in frames for owner in frame.unresolved_connects))
        )
        return effective, original_checks if callbacks else None, owners

    def _connect(self, *, deadline: Deadline = None, checkpoint: _ConnectCheckpoint | None = None) -> Any:
        effective, original_checks, owners = self._connection_inputs(deadline, checkpoint)
        try:
            _connect_checkpoint(effective, original_checks)
            original = connect_postgresql(
                **self._operation_parameters(), autocommit=False, row_factory=dict_row,
                deadline=effective, checkpoint=original_checks, unresolved_connects=owners,
                poll_interval=self._operation_policy().connect_poll_seconds,
            )
            return self._own_connection(original)
        except PostgresKernelError:
            raise
        except Exception as error:
            incident = uuid.uuid4().hex
            _LOG.error('postgres_connect_failed incident=%s type=%s', incident, type(error).__name__)
            raise PostgresKernelError('postgres_unavailable', 'PostgreSQL service is unavailable',
                                      details={'incident': incident}) from None

    def _configure_transaction(self, cursor: Any) -> Any:
        effective, original_checks, _owners = self._connection_inputs(None, None)
        guarded = _DeadlineCursor(cursor, self._operation_policy(), effective, original_checks)
        if effective is not None:
            guarded.configure()
            return guarded
        _connect_checkpoint(effective, original_checks)
        cursor.execute("SELECT set_config('lock_timeout', %s, true)", (f"{self._operation_policy().lock_timeout_ms}ms",))
        _connect_checkpoint(effective, original_checks)
        cursor.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (f"{self._operation_policy().statement_timeout_ms}ms",),
        )
        _connect_checkpoint(effective, original_checks)
        return guarded

    @staticmethod
    def _current_schema(cursor: Any) -> str:
        cursor.execute("SELECT current_schema() AS schema_name")
        row = cursor.fetchone()
        schema_name = row.get("schema_name") if isinstance(row, Mapping) else row[0]
        if not isinstance(schema_name, str) or not schema_name:
            raise PostgresKernelError("schema_unavailable", "libpq service does not select a current schema")
        _validate_text(schema_name, label="PostgreSQL schema name")
        return schema_name

    @staticmethod
    def _table_names(cursor: Any, schema_name: str) -> set[str]:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
            (schema_name,),
        )
        return {str(row.get("table_name") if isinstance(row, Mapping) else row[0]) for row in cursor.fetchall()}

    @staticmethod
    def _schema_has_relations(cursor: Any, schema_name: str) -> bool:
        cursor.execute(
            "SELECT EXISTS ("
            "SELECT 1 FROM pg_catalog.pg_depend d "
            "WHERE d.refclassid='pg_catalog.pg_namespace'::regclass "
            "AND d.refobjid=(SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s) "
            "AND d.deptype='n'"
            ") AS has_relations",
            (schema_name,),
        )
        row = cursor.fetchone()
        return bool(row.get("has_relations") if isinstance(row, Mapping) else row[0])

    @staticmethod
    def _schema_comment(cursor: Any, schema_name: str) -> object:
        cursor.execute(
            "SELECT obj_description(oid, 'pg_namespace') AS comment FROM pg_catalog.pg_namespace WHERE nspname=%s",
            (schema_name,),
        )
        row = cursor.fetchone()
        if row is None:
            raise PostgresKernelError("schema_unavailable", "PostgreSQL current schema does not exist")
        return row.get("comment") if isinstance(row, Mapping) else row[0]

    @staticmethod
    def _decode_schema_comment(comment: object) -> dict[str, Any] | None:
        if comment is None:
            return None
        try:
            if not isinstance(comment, str):
                raise PostgresKernelError("schema_metadata_drift", "PostgreSQL schema comment is not text")
            value = parse_json_bytes(comment.encode("utf-8"))
            metadata = _exact_keys(
                value,
                {"catalog_sha256", "format", "schema_sha256", "schema_version"},
                label="schema metadata",
            )
        except PostgresKernelError as exc:
            raise PostgresKernelError(
                "schema_metadata_drift",
                "PostgreSQL schema metadata is invalid",
            ) from exc
        if (
            metadata["format"] != SCHEMA_FORMAT
            or type(metadata["schema_version"]) is not int
            or metadata["schema_version"] != SCHEMA_VERSION
            or not isinstance(metadata["schema_sha256"], str)
            or not _LOWER_SHA_RE.fullmatch(metadata["schema_sha256"])
            or not isinstance(metadata["catalog_sha256"], str)
            or not _LOWER_SHA_RE.fullmatch(metadata["catalog_sha256"])
        ):
            raise PostgresKernelError("schema_metadata_drift", "PostgreSQL schema metadata is invalid")
        return metadata

    @staticmethod
    def _catalog_snapshot(cursor: Any, schema_name: str) -> dict[str, Any]:
        queries = {
            "columns": (
                "SELECT table_name,ordinal_position,column_name,data_type,udt_name,is_nullable,"
                "column_default,is_identity,identity_generation,collation_name,"
                "character_maximum_length,numeric_precision,numeric_scale,datetime_precision,"
                "is_generated,generation_expression "
                "FROM information_schema.columns WHERE table_schema=%s "
                "ORDER BY table_name,ordinal_position"
            ),
            "constraints": (
                "SELECT r.relname AS table_name,c.conname AS constraint_name,c.contype AS constraint_type,"
                "c.condeferrable AS is_deferrable,c.condeferred AS initially_deferred,"
                "c.convalidated AS is_validated,pg_get_constraintdef(c.oid,false) AS definition "
                "FROM pg_catalog.pg_constraint c "
                "JOIN pg_catalog.pg_class r ON r.oid=c.conrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace "
                "WHERE n.nspname=%s ORDER BY r.relname,c.conname"
            ),
            "indexes": (
                "SELECT tablename AS table_name,indexname AS index_name,indexdef AS definition "
                "FROM pg_catalog.pg_indexes WHERE schemaname=%s ORDER BY tablename,indexname"
            ),
            "namespace_object_counts": (
                "SELECT d.classid::regclass::text AS catalog_name,count(*)::bigint AS object_count "
                "FROM pg_catalog.pg_depend d "
                "WHERE d.refclassid='pg_catalog.pg_namespace'::regclass "
                "AND d.refobjid=(SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s) "
                "AND d.deptype='n' "
                "GROUP BY d.classid ORDER BY catalog_name"
            ),
            "relations": (
                "SELECT c.relname AS relation_name,c.relkind AS relation_kind,"
                "c.relpersistence AS persistence,c.relreplident AS replica_identity,"
                "c.relrowsecurity AS row_security,c.relforcerowsecurity AS force_row_security,"
                "c.reloptions AS relation_options,am.amname AS access_method "
                "FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "LEFT JOIN pg_catalog.pg_am am ON am.oid=c.relam "
                "WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','S','f') "
                "ORDER BY c.relname,c.relkind"
            ),
            "attributes": (
                "SELECT c.relname AS relation_name,a.attnum AS ordinal_position,a.attname AS column_name,"
                "pg_catalog.format_type(a.atttypid,a.atttypmod) AS formatted_type,"
                "a.attnotnull AS is_not_null,a.atthasdef AS has_default,a.attidentity AS identity_kind,"
                "a.attgenerated AS generated_kind,a.attstorage AS storage_kind,a.attcompression AS compression_kind,"
                "a.attstattarget AS statistics_target,a.attoptions AS attribute_options,"
                "coll.collname AS collation_name "
                "FROM pg_catalog.pg_attribute a "
                "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "LEFT JOIN pg_catalog.pg_collation coll ON coll.oid=a.attcollation "
                "WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped "
                "ORDER BY c.relname,a.attnum"
            ),
            "routines": (
                "SELECT p.proname AS routine_name,p.prokind AS routine_kind,"
                "pg_get_function_identity_arguments(p.oid) AS identity_arguments "
                "FROM pg_catalog.pg_proc p "
                "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname=%s ORDER BY p.proname,identity_arguments"
            ),
            "sequences": (
                "SELECT c.relname AS sequence_name,s.seqtypid::regtype::text AS data_type,"
                "s.seqstart AS start_value,s.seqincrement AS increment_by,"
                "s.seqmax AS maximum_value,s.seqmin AS minimum_value,"
                "s.seqcache AS cache_size,s.seqcycle AS cycles "
                "FROM pg_catalog.pg_sequence s "
                "JOIN pg_catalog.pg_class c ON c.oid=s.seqrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s ORDER BY c.relname"
            ),
            "triggers": (
                "SELECT c.relname AS table_name,t.tgname AS trigger_name,"
                "pg_get_triggerdef(t.oid,false) AS definition "
                "FROM pg_catalog.pg_trigger t "
                "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND NOT t.tgisinternal ORDER BY c.relname,t.tgname"
            ),
            "policies": (
                "SELECT tablename AS table_name,policyname AS policy_name,permissive,roles,cmd,qual,with_check "
                "FROM pg_catalog.pg_policies WHERE schemaname=%s ORDER BY tablename,policyname"
            ),
            "rules": (
                "SELECT c.relname AS relation_name,r.rulename AS rule_name,"
                "pg_get_ruledef(r.oid,false) AS definition "
                "FROM pg_catalog.pg_rewrite r "
                "JOIN pg_catalog.pg_class c ON c.oid=r.ev_class "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND r.rulename <> '_RETURN' ORDER BY c.relname,r.rulename"
            ),
            "types": (
                "SELECT t.typname AS type_name,t.typtype AS type_kind,t.typcategory AS category,"
                "t.typispreferred AS is_preferred,t.typnotnull AS is_not_null,t.typdefault AS default_value,"
                "base.typname AS base_type,element.typname AS element_type,relation.relname AS relation_name "
                "FROM pg_catalog.pg_type t "
                "JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace "
                "LEFT JOIN pg_catalog.pg_type base ON base.oid=t.typbasetype "
                "LEFT JOIN pg_catalog.pg_type element ON element.oid=t.typelem "
                "LEFT JOIN pg_catalog.pg_class relation ON relation.oid=t.typrelid "
                "WHERE n.nspname=%s ORDER BY t.typname"
            ),
        }
        snapshot: dict[str, Any] = {}
        for key, query in queries.items():
            cursor.execute(query, (schema_name,))
            rows: list[dict[str, Any]] = []
            for raw_row in cursor.fetchall():
                if not isinstance(raw_row, Mapping):
                    raise PostgresKernelError(
                        "schema_catalog_unreadable",
                        "PostgreSQL catalog did not return named columns",
                    )
                rows.append({str(column): _normalize_pg_value(value) for column, value in raw_row.items()})
            snapshot[key] = rows
        return snapshot

    @classmethod
    def _catalog_sha256(cls, cursor: Any, schema_name: str) -> str:
        return canonical_sha256(cls._catalog_snapshot(cursor, schema_name))

    @classmethod
    def _require_exact_schema(cls, cursor: Any, schema_name: str) -> None:
        tables = cls._table_names(cursor, schema_name)
        actual_catalog_sha256 = cls._catalog_sha256(cursor, schema_name)
        try:
            metadata = cls._decode_schema_comment(cls._schema_comment(cursor, schema_name))
        except PostgresKernelError as exc:
            raise PostgresKernelError(
                "schema_drift",
                "PostgreSQL target schema metadata does not match kernel v1",
                details={"actual_catalog_sha256": actual_catalog_sha256},
            ) from exc
        if (
            tables != set(ALL_TABLES)
            or metadata is None
            or metadata.get("format") != SCHEMA_FORMAT
            or metadata.get("schema_version") != SCHEMA_VERSION
            or metadata.get("schema_sha256") != schema_sql_sha256()
            or metadata.get("catalog_sha256") != actual_catalog_sha256
        ):
            raise PostgresKernelError(
                "schema_drift",
                "PostgreSQL target schema is not the exact kernel v1 catalog",
                details={"actual_catalog_sha256": actual_catalog_sha256},
            )

    @operation_method
    def initialize(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            with connection, connection.transaction():
                cursor = connection.cursor()
                cursor = self._configure_transaction(cursor)
                schema_name = self._current_schema(cursor)
                table_names = self._table_names(cursor, schema_name)
                schema_comment = self._schema_comment(cursor, schema_name)
                stock_public_default = schema_name == "public" and schema_comment == STOCK_PUBLIC_SCHEMA_COMMENT
                if table_names or (schema_comment is not None and not stock_public_default):
                    self._require_exact_schema(cursor, schema_name)
                    return {
                        "schema_sha256": schema_sql_sha256(),
                        "schema_version": SCHEMA_VERSION,
                        "status": "already_current",
                        "table_count": len(ALL_TABLES),
                    }

                if self._schema_has_relations(cursor, schema_name):
                    raise PostgresKernelError(
                        "schema_not_empty",
                        "PostgreSQL target schema contains pre-existing relations",
                    )

                try:
                    template = schema_sql_bytes().decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise PostgresKernelError(
                        "schema_resource_invalid", "Packaged PostgreSQL schema is not UTF-8"
                    ) from exc
                cursor.execute(sql.SQL(template).format(schema=sql.Identifier(schema_name)))
                catalog_sha256 = self._catalog_sha256(cursor, schema_name)
                expected_metadata = _schema_metadata(catalog_sha256=catalog_sha256)
                comment = canonical_json_bytes(expected_metadata).decode("utf-8").removesuffix("\n")
                cursor.execute(
                    sql.SQL("COMMENT ON SCHEMA {} IS {}").format(
                        sql.Identifier(schema_name),
                        sql.Literal(comment),
                    )
                )
                self._require_exact_schema(cursor, schema_name)
                return {
                    "schema_sha256": expected_metadata["schema_sha256"],
                    "schema_version": SCHEMA_VERSION,
                    "status": "initialized",
                    "table_count": len(ALL_TABLES),
                }
        except PostgresKernelError:
            raise
        except Exception as exc:  # intentional-catch: driver failures surface as PostgresKernelError
            raise _unclassified_failure("PostgreSQL initialization failed", exc) from exc

    @operation_method
    def upgrade_schema(self, *, expected_schema_sha256: str) -> dict[str, Any]:
        """Apply the supported predecessor's transition steps without rewriting domain history.

        The operator selects the exact predecessor explicitly. Ordinary startup
        still refuses drift. All checks, table changes and the existing schema
        metadata update share one transaction; no row is assigned a repository
        or application scope, and no status is rewritten, by this structural
        transition. c123 (batch design WP5 G17): SCHEMA_TRANSITIONS names the
        steps of each supported predecessor, and every step's checks run before
        any step's DDL. The meter-usage step also books the single exact owner
        exception after its locked preimage passes; it does not infer actual usage.
        """
        steps = SCHEMA_TRANSITIONS.get(expected_schema_sha256)
        if steps is None:
            raise PostgresKernelError(
                "unsupported_schema_upgrade",
                "The requested PostgreSQL predecessor has no supported transition",
                details={"supported_schema_sha256": sorted(SCHEMA_TRANSITIONS)},
            )
        connection = self._connect()
        try:
            with connection, connection.transaction():
                cursor = connection.cursor()
                cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                cursor = self._configure_transaction(cursor)
                schema_name = self._current_schema(cursor)
                initial_tables = self._table_names(cursor, schema_name)
                if initial_tables not in (set(PRE_DISPATCHER_TABLES), set(ALL_TABLES)):
                    raise PostgresKernelError(
                        "schema_upgrade_preimage_mismatch",
                        "Transition requires the complete supported predecessor; initialize an empty schema normally",
                    )
                from ._arbitration import arbitrate

                arbitrate(PostgresTransaction(self, cursor, schema_name))
                # Re-read metadata after acquiring the existing database locks.
                # A concurrent transition may have completed while this waited.
                cursor.execute(
                    sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(
                        sql.SQL(",").join(
                            sql.SQL("{}.{}").format(sql.Identifier(schema_name), sql.Identifier(table))
                            for table in (ALL_TABLES if initial_tables == set(ALL_TABLES) else PRE_DISPATCHER_TABLES)
                        )
                    )
                )
                try:
                    metadata = self._decode_schema_comment(self._schema_comment(cursor, schema_name))
                except PostgresKernelError as exc:
                    raise PostgresKernelError(
                        "schema_upgrade_preimage_mismatch", "The predecessor schema metadata is invalid"
                    ) from exc
                if metadata is not None and metadata["schema_sha256"] == schema_sql_sha256():
                    self._require_exact_schema(cursor, schema_name)
                    return {
                        "status": "already_current",
                        "schema_sha256": schema_sql_sha256(),
                        "schema_version": SCHEMA_VERSION,
                        "table_count": len(ALL_TABLES),
                    }
                if (
                    metadata is None
                    or metadata["schema_sha256"] != expected_schema_sha256
                    or metadata["catalog_sha256"] != self._catalog_sha256(cursor, schema_name)
                    or self._table_names(cursor, schema_name)
                    != set(ALL_TABLES if "dispatcher_coordination" not in steps else PRE_DISPATCHER_TABLES)
                ):
                    raise PostgresKernelError(
                        "schema_upgrade_preimage_mismatch",
                        "The PostgreSQL catalog does not match the exact supported predecessor; inspect the drift",
                    )
                approved_meter_exception = False
                if expected_schema_sha256 == METER_USAGE_BASIS_PREDECESSOR_SHA256:
                    approved_meter_exception = _approved_meter_exception_preimage(cursor, schema_name)
                if "meter_usage_basis" in steps and "dispatcher_coordination" not in steps:
                    cursor.execute(
                        "SELECT 1 FROM information_schema.columns WHERE table_schema=%s "
                        "AND table_name='dispatcher_meter' AND column_name='usage_basis'",
                        (schema_name,),
                    )
                    if cursor.fetchone() is not None:
                        raise PostgresKernelError(
                            "schema_upgrade_preimage_mismatch", "The predecessor already contains usage_basis"
                        )
                scope_pattern = r"^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$"
                if "prepared_holder" in steps and "dispatcher_coordination" not in steps:
                    from .record_types import DispatchObservation

                    cursor.execute(
                        sql.SQL(
                            "SELECT dispatch_observation FROM {}.bridge_attempts WHERE dispatch_observation IS NOT NULL"
                        ).format(sql.Identifier(schema_name))
                    )
                    for row in cursor.fetchall():
                        try:
                            DispatchObservation.model_validate(row["dispatch_observation"])
                        except ValueError as error:
                            raise PostgresKernelError(
                                "schema_upgrade_preimage_mismatch",
                                "An existing dispatch observation is not a closed supported identity",
                            ) from error
                if "repository_scope" in steps:
                    unresolved = {}
                    for table in ("specifications", "tests"):
                        cursor.execute(
                            sql.SQL(
                                "SELECT id FROM {}.{} WHERE application_scope IS NOT NULL "
                                "AND application_scope !~ %s ORDER BY id"
                            ).format(sql.Identifier(schema_name), sql.Identifier(table)),
                            (scope_pattern,),
                        )
                        unresolved[table] = [row["id"] for row in cursor.fetchall()]
                    if any(unresolved.values()):
                        raise PostgresKernelError(
                            "application_scope_reconciliation_required",
                            "Reconcile these current record scopes through the existing writer before transition; "
                            "repository or application associations are never inferred",
                            details=unresolved,
                        )
                statuses = bridge_status_check_values()
                if "bridge_status" in steps:
                    self._require_canonical_bridge_statuses(cursor, schema_name, statuses)
                if "explicit_scope" in steps:
                    # Read the exact locked predecessor before DDL. Absent new
                    # fields remain unresolved, and existing scopes are never
                    # derived from repository associations or record names.
                    scoped_rows = {}
                    for table in CURRENT_TABLES:
                        cursor.execute(
                            sql.SQL("SELECT * FROM {}.{}").format(sql.Identifier(schema_name), sql.Identifier(table))
                        )
                        scoped_rows[table] = [{"application_scope": None, **dict(row)} for row in cursor.fetchall()]
                    self._validate_import_scopes(scoped_rows, project_root=self.project_root)
                    validate_application_scope_relationships(scoped_rows)
                if "planning_estimate" in steps:
                    cursor.execute(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema=%s AND table_name='projects' AND column_name='planning_estimate'",
                        (schema_name,),
                    )
                    if cursor.fetchone() is not None:
                        raise PostgresKernelError(
                            "schema_upgrade_preimage_mismatch", "The predecessor already contains planning_estimate"
                        )
                if "repository_scope" in steps:
                    self._apply_repository_scope_step(cursor, schema_name, scope_pattern)
                if "bridge_status" in steps:
                    for table, column, _key in BRIDGE_STATUS_COLUMNS:
                        cursor.execute(
                            sql.SQL("ALTER TABLE {}.{} ADD CONSTRAINT {} CHECK ({} IN ({}))").format(
                                sql.Identifier(schema_name),
                                sql.Identifier(table),
                                sql.Identifier(f"{table}_{column}_check"),
                                sql.Identifier(column),
                                sql.SQL(", ").join(sql.Literal(status) for status in statuses),
                            )
                        )
                if "dispatcher_coordination" in steps:
                    cursor.execute(
                        sql.SQL(dispatcher_coordination_sql().decode("utf-8")).format(
                            schema=sql.Identifier(schema_name)
                        )
                    )
                if "execution_carriers" in steps:
                    self._apply_execution_carrier_step(
                        cursor, schema_name, meter_already_current="dispatcher_coordination" in steps
                    )
                if "prepared_holder" in steps:
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE {}.bridge_attempts DROP CONSTRAINT bridge_dispatch_observation_check"
                        ).format(sql.Identifier(schema_name))
                    )
                    cursor.execute(
                        sql.SQL(dispatch_observation_check_sql().decode("utf-8")).format(
                            schema=sql.Identifier(schema_name)
                        )
                    )
                if "init_subject" in steps:
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE {}.session_init_bindings DROP CONSTRAINT session_init_bindings_subject_check, "
                            "ADD CONSTRAINT session_init_bindings_subject_check CHECK ("
                            "subject ~ '^[A-Za-z][A-Za-z0-9_-]*$' AND (lower(subject) <> 'gtkb' OR subject = 'gtkb'))"
                        ).format(sql.Identifier(schema_name))
                    )
                if "explicit_scope" in steps:
                    self._apply_explicit_scope_step(cursor, schema_name, scope_pattern)
                if "planning_estimate" in steps:
                    cursor.execute(
                        sql.SQL("ALTER TABLE {}.projects ADD COLUMN planning_estimate JSONB").format(
                            sql.Identifier(schema_name)
                        )
                    )
                if "meter_usage_basis" in steps and "dispatcher_coordination" not in steps:
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE {}.dispatcher_meter ADD COLUMN " + meter_usage_basis_column_sql().decode()
                        ).format(sql.Identifier(schema_name))
                    )
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE {}.dispatcher_meter "
                            "DROP CONSTRAINT dispatcher_meter_check1, DROP CONSTRAINT dispatcher_meter_check2"
                        ).format(sql.Identifier(schema_name))
                    )
                    for before, after in (
                        ("dispatcher_meter_check3", "dispatcher_meter_check2"),
                        ("dispatcher_meter_check4", "dispatcher_meter_check3"),
                    ):
                        cursor.execute(
                            sql.SQL("ALTER TABLE {}.dispatcher_meter RENAME CONSTRAINT {} TO {}").format(
                                sql.Identifier(schema_name), sql.Identifier(before), sql.Identifier(after)
                            )
                        )
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE {}.dispatcher_meter "
                            "ADD CONSTRAINT dispatcher_meter_consumed_amount_check "
                            "CHECK (consumed_amount BETWEEN 0 AND 9007199254740991), "
                            "ADD CONSTRAINT dispatcher_meter_check1 "
                            "CHECK (held_amount + consumed_amount <= 9007199254740991)"
                        ).format(sql.Identifier(schema_name))
                    )
                if approved_meter_exception:
                    # Book the approved reserve as an exception, never measured actual usage.
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {}.dispatcher_meter SET held_amount=0,consumed_amount=reserved_amount,"
                            "settlement_state='settled',usage_basis='owner_exception' "
                            "WHERE execution_native_context_id=%s AND try_no=1 AND call_no=42 "
                            "AND unit IN ('tokens','micro_usd')"
                        ).format(sql.Identifier(schema_name)),
                        ("f550d42c-560f-40d6-a2c8-abd74b2bc8b3",),
                    )
                    if cursor.rowcount != 2:
                        raise PostgresKernelError(
                            "schema_upgrade_preimage_mismatch", "The approved meter vector changed"
                        )
                current_metadata = _schema_metadata(catalog_sha256=self._catalog_sha256(cursor, schema_name))
                cursor.execute(
                    sql.SQL("COMMENT ON SCHEMA {} IS {}").format(
                        sql.Identifier(schema_name),
                        sql.Literal(canonical_json_bytes(current_metadata).decode("utf-8").removesuffix("\n")),
                    )
                )
                self._require_exact_schema(cursor, schema_name)
                cursor.execute(
                    sql.SQL(
                        "SELECT count(*) AS count FROM {}.projects WHERE kind='project' AND repository_ref IS NULL"
                    ).format(sql.Identifier(schema_name))
                )
                return {
                    "status": "upgraded",
                    "upgraded_from": expected_schema_sha256,
                    "steps": list(steps),
                    "schema_sha256": schema_sql_sha256(),
                    "schema_version": SCHEMA_VERSION,
                    "table_count": len(ALL_TABLES),
                    "unresolved_project_repositories": int(cursor.fetchone()["count"]),
                }
        except PostgresKernelError:
            raise
        except (LockNotAvailable, DeadlockDetected, SerializationFailure) as exc:
            raise PostgresKernelError(
                "retryable_conflict",
                "PostgreSQL schema transition conflicted with another transaction; retry after it completes",
            ) from exc
        except Exception as exc:  # intentional-catch: native driver failures get a non-sensitive diagnostic
            raise _unclassified_failure("PostgreSQL schema transition failed", exc) from exc

    @staticmethod
    def _apply_explicit_scope_step(cursor: Any, schema_name: str, scope_pattern: str) -> None:
        for table in CURRENT_TABLES:
            if table not in _NEW_APPLICATION_SCOPE_TABLES:
                continue
            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {}.{} ADD COLUMN application_scope TEXT, "
                    "ADD CONSTRAINT {} CHECK (application_scope ~ {})"
                ).format(
                    sql.Identifier(schema_name),
                    sql.Identifier(table),
                    sql.Identifier(table + "_application_scope_check"),
                    sql.Literal(scope_pattern),
                )
            )

    @staticmethod
    def _apply_execution_carrier_step(cursor: Any, schema_name: str, *, meter_already_current: bool) -> None:
        """Nullable legacy defaults and exact owner shape; no historical result rewrite."""
        cursor.execute(
            sql.SQL(
                "ALTER TABLE {schema}.tests ADD COLUMN execution_evidence JSONB "
                "CHECK (execution_evidence IS NULL OR jsonb_typeof(execution_evidence)='object'); "
                "ALTER TABLE {schema}.session_init_bindings "
                "ALTER COLUMN session_context_id DROP NOT NULL, ALTER COLUMN subject DROP NOT NULL, "
                "ALTER COLUMN role DROP NOT NULL, ALTER COLUMN minimum_idempotency_identity DROP NOT NULL, "
                "ADD COLUMN execution_observation JSONB "
                "CHECK (execution_observation IS NULL OR jsonb_typeof(execution_observation)='object'), "
                "ADD CONSTRAINT session_init_bindings_initialization_shape CHECK ("
                "(session_context_id IS NOT NULL AND subject IS NOT NULL AND role IS NOT NULL "
                "AND minimum_idempotency_identity IS NOT NULL) OR ("
                "session_context_id IS NULL AND subject IS NULL AND role IS NULL "
                "AND minimum_idempotency_identity IS NULL AND execution_observation IS NOT NULL));"
            ).format(schema=sql.Identifier(schema_name))
        )
        if not meter_already_current:
            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {schema}.dispatcher_meter ALTER COLUMN attempt_id DROP NOT NULL, "
                    "ADD COLUMN execution_native_context_id TEXT "
                    "REFERENCES {schema}.session_init_bindings(native_context_id), "
                    "ADD CONSTRAINT dispatcher_meter_exact_owner CHECK ("
                    "(attempt_id IS NOT NULL AND execution_native_context_id IS NULL) OR "
                    "(attempt_id IS NULL AND execution_native_context_id IS NOT NULL))"
                ).format(schema=sql.Identifier(schema_name))
            )

    @staticmethod
    def _require_canonical_bridge_statuses(cursor: Any, schema_name: str, statuses: Sequence[str]) -> None:
        """Refuse a transition while any bridge status column holds a value outside ``statuses`` (c123; WP5 G17).

        The refusal names the column, the values and the attempts, never message content; no DDL has run. A NULL
        head_status (an attempt with no item yet) is allowed, as the constraint allows it.
        """
        violations: dict[str, Any] = {}
        for table, column, key in BRIDGE_STATUS_COLUMNS:
            cursor.execute(
                sql.SQL(
                    "SELECT {} AS attempt_id, {} AS value FROM {}.{} WHERE {} IS NOT NULL AND {} <> ALL(%s)"
                ).format(
                    sql.Identifier(key),
                    sql.Identifier(column),
                    sql.Identifier(schema_name),
                    sql.Identifier(table),
                    sql.Identifier(column),
                    sql.Identifier(column),
                ),
                (list(statuses),),
            )
            rows = cursor.fetchall()
            if rows:
                violations[f"{table}.{column}"] = {
                    "values": sorted({row["value"] for row in rows}),
                    "attempt_ids": sorted({row["attempt_id"] for row in rows}),
                }
        if violations:
            raise PostgresKernelError(
                "bridge_status_reconciliation_required",
                "Reconcile these bridge statuses through the native bridge before transition; the transition rewrites "
                "no status",
                details=violations,
            )

    @staticmethod
    def _apply_repository_scope_step(cursor: Any, schema_name: str, scope_pattern: str) -> None:
        """The repository/scope step of the 2f25 predecessor (its DDL as before c123)."""
        cursor.execute(
            sql.SQL(
                "ALTER TABLE {}.projects ADD COLUMN repository_ref TEXT CHECK ("
                "repository_ref IS NULL OR repository_ref = 'platform' OR "
                "repository_ref ~ '^application:[A-Za-z][A-Za-z0-9_-]*$'), "
                "DROP CONSTRAINT projects_check, ADD CONSTRAINT projects_check CHECK (("
                "kind = 'program' AND \"authorization\" IS NULL AND parent_project_id IS NULL "
                "AND repository_ref IS NULL) OR (kind = 'project' AND \"authorization\" IS NOT NULL "
                "AND \"authorization\" IN ('authorized', 'not authorized')))"
            ).format(sql.Identifier(schema_name))
        )
        for table in ("specifications", "tests"):
            constraint = sql.Identifier(table + "_application_scope_check")
            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {}.{} DROP CONSTRAINT {}, ADD CONSTRAINT {} CHECK (application_scope ~ {})"
                ).format(
                    sql.Identifier(schema_name),
                    sql.Identifier(table),
                    constraint,
                    constraint,
                    sql.Literal(scope_pattern),
                )
            )

    @operation_method
    def status(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            with connection, connection.transaction():
                cursor = connection.cursor()
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor = self._configure_transaction(cursor)
                schema_name = self._current_schema(cursor)
                cursor.execute("SHOW server_version_num")
                version_row = cursor.fetchone()
                version_text = (
                    version_row.get("server_version_num") if isinstance(version_row, Mapping) else version_row[0]
                )
                server_major = int(str(version_text)) // 10000
                tables = self._table_names(cursor, schema_name)
                schema_comment = self._schema_comment(cursor, schema_name)
                # A table-free public schema carrying only PostgreSQL's stock comment is an
                # uninitialized target, exactly as initialize() classifies it. Report it as
                # not ready with every table missing instead of raising metadata drift.
                stock_public_default = (
                    schema_name == "public" and not tables and schema_comment == STOCK_PUBLIC_SCHEMA_COMMENT
                )
                metadata = None if stock_public_default else self._decode_schema_comment(schema_comment)
                actual_catalog_sha256 = self._catalog_sha256(cursor, schema_name)
                cursor.execute(
                    "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=%s",
                    (schema_name,),
                )
                forbidden_columns = sorted(
                    f"{row['table_name']}.{row['column_name']}"
                    for row in cursor.fetchall()
                    if row["column_name"] in FORBIDDEN_COLUMNS
                )
                missing_tables = sorted(set(ALL_TABLES) - tables)
                unexpected_tables = sorted(tables - set(ALL_TABLES))
                forbidden_tables = sorted(tables & FORBIDDEN_TABLES)
                ready = (
                    metadata is not None
                    and metadata.get("format") == SCHEMA_FORMAT
                    and metadata.get("schema_version") == SCHEMA_VERSION
                    and metadata.get("schema_sha256") == schema_sql_sha256()
                    and metadata.get("catalog_sha256") == actual_catalog_sha256
                    and not missing_tables
                    and not unexpected_tables
                    and not forbidden_tables
                    and not forbidden_columns
                )
                return {
                    "forbidden_columns": forbidden_columns,
                    "forbidden_tables": forbidden_tables,
                    "missing_tables": missing_tables,
                    "postgresql_major_version": server_major,
                    "reachable": True,
                    "ready": ready,
                    "schema_catalog_matches": bool(
                        metadata and metadata.get("catalog_sha256") == actual_catalog_sha256
                    ),
                    "schema_catalog_sha256": actual_catalog_sha256,
                    "schema_sha256": metadata.get("schema_sha256") if metadata else None,
                    "schema_version": metadata.get("schema_version") if metadata else None,
                    "timeout_posture": {
                        "connect_timeout_seconds": None,
                        "lock_timeout_ms": self._operation_policy().lock_timeout_ms,
                        "statement_timeout_ms": self._operation_policy().statement_timeout_ms,
                    },
                    "unexpected_tables": unexpected_tables,
                }
        except PostgresKernelError:
            raise
        except Exception as exc:  # intentional-catch: driver failures surface as PostgresKernelError
            raise _unclassified_failure("PostgreSQL status failed", exc) from exc

    @contextmanager
    def _inspect_sqlite_snapshot(
        self,
        *,
        sqlite_snapshot: Path,
        live_sqlite_source: Path | None,
    ) -> Iterator[tuple[sqlite3.Connection, dict[str, Any]]]:
        try:
            if sqlite_snapshot.is_symlink():
                raise PostgresKernelError("invalid_snapshot", "SQLite snapshot must not be a symbolic link")
            snapshot = sqlite_snapshot.resolve(strict=True)
            if not snapshot.is_file():
                raise PostgresKernelError("invalid_snapshot", "SQLite snapshot must be one regular non-symlink file")
            if live_sqlite_source is not None:
                live_source = live_sqlite_source.resolve(strict=False)
                same_file = snapshot == live_source
                if live_source.exists():
                    same_file = same_file or os.path.samefile(snapshot, live_source)
                if same_file:
                    raise PostgresKernelError(
                        "live_source_forbidden",
                        "The live SQLite source cannot be exported directly",
                    )
            for suffix in ("-wal", "-shm"):
                if snapshot.with_name(snapshot.name + suffix).exists():
                    raise PostgresKernelError(
                        "snapshot_sidecar_present",
                        "SQLite snapshot has an adjacent WAL or SHM sidecar",
                    )
            initial_size = snapshot.stat().st_size
            if initial_size <= 0:
                raise PostgresKernelError("invalid_snapshot", "SQLite snapshot must have a positive byte size")
            initial_hash = _hash_file(snapshot)
        except PostgresKernelError:
            raise
        except OSError as exc:
            raise PostgresKernelError(
                "snapshot_preflight_failed",
                "SQLite snapshot filesystem preflight failed",
            ) from exc

        connector = self._sqlite_connector or sqlite3.connect
        uri = snapshot.as_uri() + "?mode=ro&immutable=1"
        connection: sqlite3.Connection | None = None
        primary_read_failed = False
        try:
            connection = connector(uri, uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
                raise PostgresKernelError("snapshot_read_failed", "SQLite query_only could not be enabled")
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
            if [row[0] for row in integrity_rows] != ["ok"]:
                raise PostgresKernelError(
                    "snapshot_integrity_failed", "SQLite snapshot integrity_check did not return ok"
                )
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if isinstance(user_version, bool) or not isinstance(user_version, int) or user_version < 0:
                raise PostgresKernelError(
                    "snapshot_read_failed",
                    "SQLite snapshot user_version must be a nonnegative integer",
                )
            inventory = _sqlite_inventory(connection)
            inventory_names = {row["name"] for row in inventory["tables"]}
            missing = set(MIGRATION_TABLES) - inventory_names
            unclassified = inventory_names - SOURCE_TABLES
            if missing or unclassified:
                raise PostgresKernelError(
                    "snapshot_table_set_mismatch",
                    "SQLite source contains missing or unclassified tables",
                    details={
                        "missing": sorted(missing),
                        "unclassified": sorted(unclassified),
                    },
                )
            source = {
                "expected_pragma_user_version": user_version,
                "expected_table_inventory_sha256": canonical_sha256(inventory),
                "sqlite_snapshot_sha256": initial_hash,
                "sqlite_snapshot_size_bytes": initial_size,
            }
            yield connection, source
        except PostgresKernelError:
            primary_read_failed = True
            raise
        except sqlite3.Error as exc:
            primary_read_failed = True
            raise PostgresKernelError("snapshot_read_failed", "Immutable SQLite snapshot read failed") from exc
        except Exception:
            primary_read_failed = True
            raise
        finally:
            if connection is not None:
                try:
                    connection.close()
                except (sqlite3.Error, OSError) as exc:
                    if not primary_read_failed:
                        raise PostgresKernelError(
                            "snapshot_read_failed",
                            "Immutable SQLite snapshot close failed",
                        ) from exc
        try:
            for suffix in ("-wal", "-shm"):
                if snapshot.with_name(snapshot.name + suffix).exists():
                    raise PostgresKernelError(
                        "snapshot_sidecar_present",
                        "SQLite snapshot gained a WAL or SHM sidecar",
                    )
            if snapshot.stat().st_size != initial_size or _hash_file(snapshot) != initial_hash:
                raise PostgresKernelError("snapshot_changed", "SQLite snapshot changed during immutable read")
        except PostgresKernelError:
            raise
        except OSError as exc:
            raise PostgresKernelError(
                "snapshot_postflight_failed",
                "SQLite snapshot filesystem postflight failed",
            ) from exc

    @operation_method
    def preflight_export_current(
        self,
        *,
        sqlite_snapshot: Path,
        live_sqlite_source: Path | None,
    ) -> dict[str, Any]:
        source: dict[str, Any]
        with self._inspect_sqlite_snapshot(
            sqlite_snapshot=sqlite_snapshot,
            live_sqlite_source=live_sqlite_source,
        ) as (_connection, inspected_source):
            source = dict(inspected_source)
        return source

    @operation_method
    def export_current(
        self,
        *,
        sqlite_snapshot: Path,
        transform_plan: Path,
        output: Path,
        live_sqlite_source: Path | None,
    ) -> dict[str, Any]:
        try:
            plan_raw = transform_plan.read_bytes()
            plan_value = parse_json_bytes(plan_raw)
        except OSError as exc:
            raise PostgresKernelError("transform_plan_unreadable", "Transform plan cannot be read") from exc
        plan = validate_transform_plan(plan_value)
        if plan_raw != canonical_json_bytes(plan):
            raise PostgresKernelError("transform_plan_not_canonical", "Transform plan bytes are not canonical")

        expected_source = plan["source"]
        with self._inspect_sqlite_snapshot(
            sqlite_snapshot=sqlite_snapshot,
            live_sqlite_source=live_sqlite_source,
        ) as (connection, actual_source):
            if (
                actual_source["sqlite_snapshot_size_bytes"] != expected_source["sqlite_snapshot_size_bytes"]
                or actual_source["sqlite_snapshot_sha256"] != expected_source["sqlite_snapshot_sha256"]
            ):
                raise PostgresKernelError(
                    "snapshot_preimage_mismatch",
                    "SQLite snapshot size or SHA-256 does not match plan",
                )
            if actual_source["expected_pragma_user_version"] != expected_source["expected_pragma_user_version"]:
                raise PostgresKernelError(
                    "snapshot_user_version_mismatch",
                    "SQLite snapshot user_version drifted",
                )
            if actual_source["expected_table_inventory_sha256"] != expected_source["expected_table_inventory_sha256"]:
                raise PostgresKernelError(
                    "snapshot_inventory_mismatch",
                    "SQLite table inventory drifted",
                )
            source_rows = {name: _current_sqlite_rows(connection, name) for name in MIGRATION_TABLES}
            transformed = _transform_source_rows(source_rows, plan)
            manifest = normalize_manifest(
                {"format": CURRENT_FORMAT, "schema_version": SCHEMA_VERSION, "tables": transformed}
            )
            payload = canonical_json_bytes(manifest)

        publication = _publish_bytes(output, payload)
        return {
            "manifest_sha256": hashlib.sha256(payload).hexdigest(),
            "row_count": sum(len(rows) for rows in manifest["tables"].values()),
            "status": publication,
        }

    @staticmethod
    def _adapt_value(column: str, value: object, spec: TableSpec) -> object:
        if column in spec.json_columns:
            return Jsonb(value, dumps=_postgres_json_dumps) if value is not None else None
        return value

    @staticmethod
    def _readback_with_cursor(cursor: Any, schema_name: str) -> dict[str, Any]:
        tables: dict[str, list[dict[str, Any]]] = {}
        for table_name in CURRENT_TABLES:
            spec = TABLE_SPECS[table_name]
            query = sql.SQL("SELECT {} FROM {}.{}").format(
                _select_columns(spec),
                sql.Identifier(schema_name),
                sql.Identifier(table_name),
            )
            cursor.execute(query)
            rows = []
            for raw_row in cursor.fetchall():
                rows.append(_normalize_pg_row(dict(raw_row), spec))
            rows.sort(key=lambda row: _identity_key(spec, row))
            tables[table_name] = rows
        cursor.execute(
            sql.SQL("SELECT {} FROM {}.session_init_bindings").format(
                sql.SQL(",").join(
                    sql.SQL("execution_observation::text AS execution_observation")
                    if column == "execution_observation"
                    else sql.Identifier(column)
                    for column in BINDING_COLUMNS
                ),
                sql.Identifier(schema_name),
            )
        )
        bindings = []
        for raw in cursor.fetchall():
            row = dict(raw)
            if row["execution_observation"] is not None:
                row["execution_observation"] = _decode_pg_json(
                    row["execution_observation"], label="session_init_bindings.execution_observation"
                )
            bindings.append(row)
        tables["session_init_bindings"] = normalize_bindings(bindings)
        return normalize_manifest(
            {"format": CURRENT_FORMAT, "schema_version": SCHEMA_VERSION, "tables": tables}, require_version_one=False
        )

    @staticmethod
    def _row_count(manifest: Mapping[str, Any]) -> int:
        return sum(len(rows) for rows in manifest["tables"].values())

    @staticmethod
    def _history_matches_manifest(cursor: Any, schema_name: str, manifest: Mapping[str, Any]) -> bool:
        cursor.execute(
            sql.SQL(
                "SELECT record_type,record_id::text AS record_id,prior_version,new_version,"
                "prior_state::text AS prior_state,new_state::text AS new_state "
                "FROM {}.record_history ORDER BY history_id"
            ).format(sql.Identifier(schema_name))
        )
        actual_rows = [dict(row) for row in cursor.fetchall()]
        expected: dict[tuple[str, bytes], bytes] = {}
        for table_name in CURRENT_TABLES:
            spec = TABLE_SPECS[table_name]
            for row in manifest["tables"][table_name]:
                identity = {column: row[column] for column in spec.identity_columns}
                key = (table_name, canonical_json_bytes(identity))
                expected[key] = canonical_json_bytes(row)
        if len(actual_rows) != len(expected):
            return False
        observed: set[tuple[str, bytes]] = set()
        for history in actual_rows:
            record_type = history.get("record_type")
            if not isinstance(record_type, str):
                return False
            try:
                record_id = _decode_pg_json(history.get("record_id"), label="record_history.record_id")
                new_state = _decode_pg_json(history.get("new_state"), label="record_history.new_state")
                key = (record_type, canonical_json_bytes(record_id))
                state_bytes = canonical_json_bytes(new_state)
            except PostgresKernelError:
                return False
            if (
                key in observed
                or expected.get(key) != state_bytes
                or history.get("prior_version") is not None
                or history.get("new_version") != 1
                or history.get("prior_state") is not None
            ):
                return False
            observed.add(key)
        return observed == set(expected)

    @staticmethod
    def _validate_import_scopes(tables: Mapping[str, list[dict[str, Any]]], *, project_root: Path | None) -> None:
        for table in CURRENT_TABLES:
            for row in tables[table]:
                scope = row.get("application_scope")
                if scope is None:
                    continue  # Historical unresolved state is preserved, not classified.
                try:
                    validate_application_scope(project_root, scope)
                except ApplicationRegistryError as error:
                    raise PostgresKernelError(
                        "invalid_application_scope",
                        str(error),
                        details={
                            "table": table,
                            "identity": {column: row[column] for column in TABLE_SPECS[table].identity_columns},
                            "application_scope": scope,
                        },
                    ) from error

    @operation_method
    def import_current(
        self, *, input_path: Path, actor: str, reason: str, project_root: Path | None = None
    ) -> dict[str, Any]:
        _opaque_id(actor, label="actor")
        _opaque_id(reason, label="reason")
        try:
            raw = input_path.read_bytes()
        except OSError as exc:
            raise PostgresKernelError("manifest_unreadable", "Migration manifest cannot be read") from exc
        supplied = parse_json_bytes(raw)
        manifest = normalize_manifest(supplied)
        canonical = canonical_json_bytes(manifest)
        # Only additive missing scope fields have a historical row-shape alias.
        # Preserve the existing canonical ordering and scalar normalization
        # checks for every old field, including timestamps and JSON values.
        input_tables = {table: [dict(row) for row in rows] for table, rows in manifest["tables"].items()}
        for table in _NEW_APPLICATION_SCOPE_TABLES:
            supplied_identities = {_identity_key(TABLE_SPECS[table], row): row for row in supplied["tables"][table]}
            for row in input_tables[table]:
                if "application_scope" not in supplied_identities[_identity_key(TABLE_SPECS[table], row)]:
                    del row["application_scope"]
        input_shape = {**manifest, "tables": input_tables}
        if raw != canonical_json_bytes(input_shape):
            raise PostgresKernelError("manifest_not_canonical", "Migration manifest bytes are not canonical")
        # Import is a write boundary. Pure normalization/readback never depends
        # on today's catalog, so historical observation remains possible.
        self._validate_import_scopes(manifest["tables"], project_root=project_root)

        connection = self._connect()
        try:
            with connection, connection.transaction():
                cursor = connection.cursor()
                cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                cursor = self._configure_transaction(cursor)
                schema_name = self._current_schema(cursor)
                self._require_exact_schema(cursor, schema_name)
                # A migration owns one bounded target transaction, not live
                # agent work. After these locks, READ COMMITTED sees any writer
                # that finished while we waited; no stale snapshot can hide it.
                cursor.execute(
                    sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
                        sql.SQL(",").join(
                            sql.SQL("{}.{}").format(sql.Identifier(schema_name), sql.Identifier(table))
                            for table in ALL_TABLES
                        )
                    )
                )
                for table in COORDINATION_TABLES:
                    if table == "session_init_bindings":
                        continue  # Immutable attribution is part of this import, not disposable bridge state.
                    cursor.execute(
                        sql.SQL("SELECT 1 FROM {}.{} LIMIT 1").format(
                            sql.Identifier(schema_name), sql.Identifier(table)
                        )
                    )
                    if cursor.fetchone() is not None:
                        raise PostgresKernelError(
                            "target_coordination_not_empty",
                            "Migration requires an empty target without existing coordination state",
                            details={"table": table},
                        )
                cursor.execute(
                    sql.SQL("SELECT count(*) AS count FROM {}.record_history").format(sql.Identifier(schema_name))
                )
                history_count = int(cursor.fetchone()["count"])
                current = self._readback_with_cursor(cursor, schema_name)
                if (
                    current["tables"]["session_init_bindings"]
                    and current["tables"]["session_init_bindings"] != manifest["tables"]["session_init_bindings"]
                ):
                    raise PostgresKernelError(
                        "target_coordination_not_empty",
                        "The target contains different immutable session bindings",
                        details={"table": "session_init_bindings"},
                    )
                current_count = self._row_count(current)
                expected_count = self._row_count(manifest)
                if canonical_json_bytes(current) == canonical and self._history_matches_manifest(
                    cursor,
                    schema_name,
                    manifest,
                ):
                    return {
                        "manifest_sha256": hashlib.sha256(canonical).hexdigest(),
                        "row_count": expected_count,
                        "status": "already_current",
                    }
                if current_count or history_count:
                    raise PostgresKernelError(
                        "target_not_empty", "PostgreSQL target is partial or differs from manifest"
                    )

                for row in manifest["tables"]["session_init_bindings"]:
                    cursor.execute(
                        sql.SQL("INSERT INTO {}.session_init_bindings ({}) VALUES ({})").format(
                            sql.Identifier(schema_name),
                            sql.SQL(",").join(sql.Identifier(column) for column in BINDING_COLUMNS),
                            sql.SQL(",").join(sql.Placeholder() for _ in BINDING_COLUMNS),
                        ),
                        [
                            Jsonb(row[column], dumps=_postgres_json_dumps)
                            if column == "execution_observation" and row[column] is not None
                            else row[column]
                            for column in BINDING_COLUMNS
                        ],
                    )
                for table_name in CURRENT_TABLES:
                    spec = TABLE_SPECS[table_name]
                    for row in manifest["tables"][table_name]:
                        insert = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                            sql.Identifier(schema_name),
                            sql.Identifier(table_name),
                            sql.SQL(",").join(sql.Identifier(column) for column in spec.columns),
                            sql.SQL(",").join(sql.Placeholder() for _ in spec.columns),
                        )
                        values = [self._adapt_value(column, row[column], spec) for column in spec.columns]
                        cursor.execute(insert, values)
                        identity = {column: row[column] for column in spec.identity_columns}
                        cursor.execute(
                            sql.SQL(
                                "INSERT INTO {}.record_history "
                                "(record_type,record_id,prior_version,new_version,prior_state,new_state,actor,reason) "
                                "VALUES (%s,%s,NULL,1,NULL,%s,%s,%s)"
                            ).format(sql.Identifier(schema_name)),
                            (
                                table_name,
                                Jsonb(identity, dumps=_postgres_json_dumps),
                                Jsonb(row, dumps=_postgres_json_dumps),
                                actor,
                                reason,
                            ),
                        )
                readback = self._readback_with_cursor(cursor, schema_name)
                if canonical_json_bytes(readback) != canonical:
                    raise PostgresKernelError("import_readback_mismatch", "In-transaction PostgreSQL readback differs")
                return {
                    "manifest_sha256": hashlib.sha256(canonical).hexdigest(),
                    "row_count": expected_count,
                    "status": "imported",
                }
        except PostgresKernelError:
            raise
        except (SerializationFailure, DeadlockDetected, LockNotAvailable, UniqueViolation) as exc:
            raise PostgresKernelError(
                "retryable_conflict", "PostgreSQL import encountered a retryable conflict"
            ) from exc
        except Exception as exc:  # intentional-catch: driver failures surface as PostgresKernelError
            raise _unclassified_failure("PostgreSQL import failed", exc) from exc

    @operation_method
    def readback_current(self, *, output: Path) -> dict[str, Any]:
        connection = self._connect()
        try:
            with connection, connection.transaction():
                cursor = connection.cursor()
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor = self._configure_transaction(cursor)
                schema_name = self._current_schema(cursor)
                self._require_exact_schema(cursor, schema_name)
                manifest = self._readback_with_cursor(cursor, schema_name)
                payload = canonical_json_bytes(manifest)
        except PostgresKernelError:
            raise
        except Exception as exc:  # intentional-catch: driver failures surface as PostgresKernelError
            raise _unclassified_failure("PostgreSQL readback failed", exc) from exc
        publication = _publish_bytes(output, payload)
        return {
            "manifest_sha256": hashlib.sha256(payload).hexdigest(),
            "row_count": self._row_count(manifest),
            "status": publication,
        }

    @operation_method
    def mutate_current(
        self,
        *,
        table: str,
        identity: Mapping[str, str],
        expected_version: int,
        new_state: Mapping[str, Any],
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        """Apply one trusted-service current-row CAS with literal row locking."""
        self._validate_mutation(table, identity, expected_version, new_state, actor, reason)
        with self.transaction(serializable=False) as transaction:
            return transaction.mutate(
                table=table,
                identity=identity,
                expected_version=expected_version,
                new_state=new_state,
                actor=actor,
                reason=reason,
            )

    @contextmanager
    def transaction(
        self,
        *,
        read_only: bool = False,
        serializable: bool = True,
        deadline: Deadline = None,
        checkpoint: _ConnectCheckpoint | None = None,
    ) -> Iterator[PostgresTransaction]:
        with self._owner_lock:
            if getattr(self._operation_local, 'frame', None) is None:
                self._check_admission()
        _connect_checkpoint(checked(deadline), checkpoint)
        with self.operation_scope():
            """Group a domain operation and its readback in one native transaction.

            Serializable writes prevent write skew between membership and project
            operations. Read-only requests see one repeatable canonical snapshot.
            Conflicts are returned to the caller; effects are never retried blindly.
            """
            with self.connection_scope(deadline=deadline, checkpoint=checkpoint):
                effective, original_checks, _owners = self._connection_inputs(None, None)
                connection = self._connect()
                try:
                    with connection:
                        _connect_checkpoint(effective, original_checks)
                        with connection.transaction():
                            _connect_checkpoint(effective, original_checks)
                            cursor = connection.cursor()
                            if read_only:
                                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                            elif serializable:
                                cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                            else:
                                # A single-row CAS waits on that row and compares its latest
                                # version. Multi-record domain operations use serializable.
                                cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                            _connect_checkpoint(effective, original_checks)
                            cursor = self._configure_transaction(cursor)
                            _connect_checkpoint(effective, original_checks)
                            schema_name = self._current_schema(cursor)
                            self._require_exact_schema(cursor, schema_name)
                            _connect_checkpoint(effective, original_checks)
                            yield PostgresTransaction(self, cursor, schema_name)
                            _connect_checkpoint(effective, original_checks)
                except PostgresKernelError:
                    raise
                except (SerializationFailure, DeadlockDetected, LockNotAvailable, UniqueViolation) as exc:
                    raise PostgresKernelError(
                        "retryable_conflict", "Concurrent canonical state changed; read current state before retrying"
                    ) from exc
                except (CheckViolation, ForeignKeyViolation, NotNullViolation, DataError) as exc:
                    raise PostgresKernelError("invalid_state", "PostgreSQL rejected the domain state") from exc
                except Exception as exc:  # intentional-catch: preserve typed controls; sanitize other failures
                    raise _unclassified_failure("PostgreSQL domain operation failed", exc) from exc

    @staticmethod
    def _validate_mutation(
        table: str,
        identity: Mapping[str, str],
        expected_version: int,
        new_state: Mapping[str, Any],
        actor: str,
        reason: str,
    ) -> tuple[TableSpec, dict[str, str], dict[str, Any]]:
        if table not in TABLE_SPECS:
            raise PostgresKernelError("unknown_record_type", "Record type is not part of the PostgreSQL kernel")
        spec = TABLE_SPECS[table]
        if set(identity) != set(spec.identity_columns):
            raise PostgresKernelError("invalid_identity", "Record identity does not match its table")
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 0
            or expected_version >= PG_INTEGER_MAX
        ):
            raise PostgresKernelError(
                "invalid_expected_version",
                "expected_version must permit a PostgreSQL integer successor",
            )
        try:
            normalized_identity = {
                column: _opaque_id(identity[column], label=f"{table}.{column}") for column in spec.identity_columns
            }
        except PostgresKernelError as exc:
            raise PostgresKernelError("invalid_identity", "Record identity values are invalid") from exc
        candidate = dict(new_state)
        if set(candidate) != set(spec.columns):
            raise PostgresKernelError("invalid_state", "Complete current-row state is required")
        candidate["version"] = expected_version + 1
        try:
            candidate = _normalize_manifest_row(table, candidate, require_version_one=False)
        except PostgresKernelError as exc:
            raise PostgresKernelError("invalid_state", "Current-row state is invalid") from exc
        for column, value in normalized_identity.items():
            if candidate.get(column) != value:
                raise PostgresKernelError("invalid_state", "State identity differs from requested identity")
        _opaque_id(actor, label="actor")
        _opaque_id(reason, label="reason")

        return spec, normalized_identity, candidate

    def _mutate_with_cursor(
        self,
        cursor: Any,
        schema_name: str,
        *,
        table: str,
        identity: Mapping[str, str],
        expected_version: int,
        new_state: Mapping[str, Any],
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        """Write and verify a current row inside the caller's transaction."""
        spec, normalized_identity, candidate = self._validate_mutation(
            table,
            identity,
            expected_version,
            new_state,
            actor,
            reason,
        )
        where = sql.SQL(" AND ").join(
            sql.SQL("{}=%s").format(sql.Identifier(column)) for column in spec.identity_columns
        )
        identity_values = [normalized_identity[column] for column in spec.identity_columns]
        cursor.execute(
            sql.SQL("SELECT {} FROM {}.{} WHERE {} FOR UPDATE").format(
                _select_columns(spec),
                sql.Identifier(schema_name),
                sql.Identifier(table),
                where,
            ),
            identity_values,
        )
        existing_raw = cursor.fetchone()
        if existing_raw is None:
            if expected_version != 0:
                raise PostgresKernelError("cas_conflict", "Current record does not match expected version")
            cursor.execute(
                sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                    sql.Identifier(schema_name),
                    sql.Identifier(table),
                    sql.SQL(",").join(sql.Identifier(column) for column in spec.columns),
                    sql.SQL(",").join(sql.Placeholder() for _ in spec.columns),
                ),
                [self._adapt_value(column, candidate[column], spec) for column in spec.columns],
            )
            prior: dict[str, Any] | None = None
            cursor.execute(
                sql.SQL(
                    "INSERT INTO {}.record_history "
                    "(record_type,record_id,prior_version,new_version,prior_state,new_state,actor,reason) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
                ).format(sql.Identifier(schema_name)),
                (
                    table,
                    Jsonb(normalized_identity, dumps=_postgres_json_dumps),
                    None,
                    candidate["version"],
                    None,
                    Jsonb(candidate, dumps=_postgres_json_dumps),
                    actor,
                    reason,
                ),
            )
        else:
            prior = _normalize_pg_row(dict(existing_raw), spec)
            if prior.get("version") != expected_version:
                raise PostgresKernelError("cas_conflict", "Current record does not match expected version")
            cursor.execute(
                sql.SQL(
                    "INSERT INTO {}.record_history "
                    "(record_type,record_id,prior_version,new_version,prior_state,new_state,actor,reason) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
                ).format(sql.Identifier(schema_name)),
                (
                    table,
                    Jsonb(normalized_identity, dumps=_postgres_json_dumps),
                    prior["version"],
                    candidate["version"],
                    Jsonb(prior, dumps=_postgres_json_dumps),
                    Jsonb(candidate, dumps=_postgres_json_dumps),
                    actor,
                    reason,
                ),
            )
            assignments = [column for column in spec.columns if column not in spec.identity_columns]
            cursor.execute(
                sql.SQL("UPDATE {}.{} SET {} WHERE {}").format(
                    sql.Identifier(schema_name),
                    sql.Identifier(table),
                    sql.SQL(",").join(sql.SQL("{}=%s").format(sql.Identifier(column)) for column in assignments),
                    where,
                ),
                [self._adapt_value(column, candidate[column], spec) for column in assignments] + identity_values,
            )
        cursor.execute(
            sql.SQL("SELECT {} FROM {}.{} WHERE {}").format(
                _select_columns(spec),
                sql.Identifier(schema_name),
                sql.Identifier(table),
                where,
            ),
            identity_values,
        )
        readback = cursor.fetchone()
        if readback is None:
            raise PostgresKernelError(
                "mutation_readback_mismatch",
                "PostgreSQL current-row readback is missing",
            )
        normalized_readback = _normalize_pg_row(dict(readback), spec)
        if canonical_json_bytes(normalized_readback) != canonical_json_bytes(candidate):
            raise PostgresKernelError(
                "mutation_readback_mismatch",
                "PostgreSQL current-row readback differs from requested state",
            )
        return {"record": normalized_readback, "status": "updated" if prior else "created"}


class PostgresTransaction:
    """Internal row primitives; public callers use typed domain services."""

    def __init__(self, kernel: PostgresKernel, cursor: Any, schema: str) -> None:
        self.kernel = kernel
        self.cursor = cursor
        self.schema = schema

    def get(self, table: str, identity: Mapping[str, str], *, lock: bool = False) -> dict[str, Any] | None:
        spec = TABLE_SPECS[table]
        if set(identity) != set(spec.identity_columns):
            raise PostgresKernelError("invalid_identity", "Record identity does not match its domain")
        where = sql.SQL(" AND ").join(sql.SQL("{}=%s").format(sql.Identifier(c)) for c in identity)
        self.cursor.execute(
            sql.SQL("SELECT {} FROM {}.{} WHERE {}{}").format(
                _select_columns(spec),
                sql.Identifier(self.schema),
                sql.Identifier(table),
                where,
                sql.SQL(" FOR UPDATE" if lock else ""),
            ),
            list(identity.values()),
        )
        row = self.cursor.fetchone()
        return _normalize_pg_row(dict(row), spec) if row else None

    def list(
        self,
        table: str,
        *,
        filters: Mapping[str, Any] | None = None,
        after: str | None = None,
        limit: int,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        spec = TABLE_SPECS[table]
        if spec.identity_columns != ("id",) or not 1 <= limit <= 1000:
            raise PostgresKernelError("invalid_query", "This query requires an id domain and a limit from 1 to 1000")
        terms: list[sql.Composable] = []
        values = []
        for column, value in (filters or {}).items():
            if column not in spec.columns or column in spec.json_columns:
                raise PostgresKernelError("invalid_query", "Unsupported domain filter")
            terms.append(sql.SQL("{} IS NOT DISTINCT FROM %s").format(sql.Identifier(column)))
            values.append(value)
        if after is not None:
            terms.append(sql.SQL('id COLLATE "C" > %s'))
            values.append(after)
        if search is not None:
            search_columns = [
                c
                for c in ("title", "name", "description", "purpose", "canonical_term", "definition")
                if c in spec.columns
            ]
            if not search_columns:
                raise PostgresKernelError("invalid_query", "Search is unavailable for this domain")
            terms.append(
                sql.SQL("({})").format(
                    sql.SQL(" OR ").join(
                        sql.SQL("strpos(lower(coalesce({},'')),lower(%s)) > 0").format(sql.Identifier(c))
                        for c in search_columns
                    )
                )
            )
            values.extend([search] * len(search_columns))
        self.cursor.execute(
            sql.SQL('SELECT {} FROM {}.{} WHERE {} ORDER BY id COLLATE "C" LIMIT %s').format(
                _select_columns(spec),
                sql.Identifier(self.schema),
                sql.Identifier(table),
                sql.SQL(" AND ").join(terms) if terms else sql.SQL("true"),
            ),
            [*values, limit],
        )
        return [_normalize_pg_row(dict(row), spec) for row in self.cursor.fetchall()]

    def mutate(self, **request: Any) -> dict[str, Any]:
        return self.kernel._mutate_with_cursor(self.cursor, self.schema, **request)

    def history(self, table: str, identity: Mapping[str, str]) -> Sequence[dict[str, Any]]:
        """Every recorded change of one identity in commit order (version chain with actor, time and reason)."""
        spec = TABLE_SPECS[table]
        if set(identity) != set(spec.identity_columns):
            raise PostgresKernelError("invalid_identity", "Record identity does not match its domain")
        normalized = {
            column: _opaque_id(identity[column], label=f"{table}.{column}") for column in spec.identity_columns
        }
        self.cursor.execute(
            sql.SQL(
                "SELECT history_id,prior_version,new_version,new_state::text AS new_state,actor,"
                "changed_at,reason FROM {}.record_history WHERE record_type=%s AND record_id=%s ORDER BY history_id"
            ).format(sql.Identifier(self.schema)),
            (table, Jsonb(normalized, dumps=_postgres_json_dumps)),
        )
        rows = []
        for row in self.cursor.fetchall():
            entry = dict(row)
            entry["new_state"] = _decode_pg_json(entry["new_state"], label="record_history.new_state")
            changed_at = entry["changed_at"]
            entry["changed_at"] = _normalize_pg_value(changed_at)
            rows.append(entry)
        return rows
