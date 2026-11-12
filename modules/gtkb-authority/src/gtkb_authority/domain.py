"""Typed native knowledge and work-planning services.

The service owns PostgreSQL connections. Clients send domain operations, never
SQL, table names, storage paths, or database credentials. A domain mutation and
its resulting current-state/history changes commit together.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn
from uuid import uuid4

from psycopg import sql
from pydantic import Field, field_validator

from .catalog import (
    ApplicationRegistryError,
    resolve_project_repository,
    validate_application_scope,
)
from .kernel import (
    APPLICATION_SCOPE_TABLES,
    CURRENT_TABLES,
    TABLE_SPECS,
    PostgresKernel,
    PostgresKernelError,
    PostgresTransaction,
    canonical_json_bytes,
    dependency_shape,
    normalize_bindings,
    validate_application_scope_relationships,
    validate_project_dependencies,
    validate_work_item_dependencies,
)
from .planning import (
    PlanningError,
    checkpoint,
    estimate_bases,
    forecast_change,
    forecast_report,
    goal_references,
    pace_bases,
    read_plan,
    revision_fields,
    validate_estimate,
)
from ._record_paths import registry_path_observations

from gtkb_protocol import Identifier, Text, Scope, Request, Mutation, WireJSON
from ._lifetime import operation_method

HistoricalCommitValidator = Callable[[Path, dict[str, WireJSON], str], str]


class SpecFields(Request):
    title: Text | None = None
    description: str | None = None
    status: Literal["active", "superseded", "retired"] = "active"
    type: Text | None = None
    priority: str | None = None
    scope: str | None = None
    section: str | None = None
    handle: str | None = None
    tags: list[str] | None = None
    assertions: list[dict[str, Any]] | None = None
    authority: str | None = None
    provisional_until: Identifier | None = None
    constraints: dict[str, Any] | list[Any] | None = None
    affected_by: list[Identifier] | None = None
    testability: Literal["automatable", "observable", "structural", "untestable"] | None = None
    source_paths: list[str] | None = None
    parent: Identifier | None = None
    application_scope: Scope | None = None
    # `true` asserts that the implementation is verified now (the service stamps the time; a client
    # timestamp is refused by this contract, as retired_at is); `null` clears the marker.
    implementation_verified_at: bool | None = None

    @field_validator("implementation_verified_at")
    @classmethod
    def _assertion_or_clear(cls, value: bool | None) -> bool | None:
        if value is False:
            raise ValueError("implementation_verified_at accepts true (assert now) or null (clear)")
        return value


class SpecMutation(Mutation):
    fields: SpecFields


class TermFields(Request):
    application_scope: Scope | None = None
    canonical_term: Text | None = None
    definition: Text | None = None
    authority_level: Literal["platform_core", "adopter_extension", "project_local"] | None = None
    scope: Text | None = None
    accepted_synonyms: list[Text] | None = None
    discouraged_synonyms: list[Text] | None = None
    linked_artifacts: list[Text] | None = None
    linked_services: list[Text] | None = None
    usage_examples: list[Text] | None = None
    forbidden_uses: list[Text] | None = None
    lifecycle_status: Literal["candidate", "active", "deprecated", "retired"] | None = None
    source_authority: Text | None = None


class TermMutation(Mutation):
    fields: TermFields


class TestFields(Request):
    title: Text | None = None
    description: str | None = None
    test_type: Text | None = None
    spec_id: Identifier | None = None
    test_file: str | None = None
    test_class: str | None = None
    test_function: str | None = None
    expected_outcome: Text | None = None
    application_scope: Scope | None = None


class TestMutation(Mutation):
    fields: TestFields


class TestPlanFields(Request):
    application_scope: Scope | None = None
    title: Text | None = None
    description: str | None = None
    status: Text | None = None


class TestPlanMutation(Mutation):
    fields: TestPlanFields


class TestPhaseFields(Request):
    application_scope: Scope | None = None
    plan_id: Identifier | None = None
    phase_order: int | None = None
    title: Text | None = None
    description: str | None = None
    gate_criteria: Text | None = None
    test_ids: list[Identifier] | None = None


class TestPhaseMutation(Mutation):
    fields: TestPhaseFields


class HarnessFields(Request):
    application_scope: Scope | None = None
    harness_name: Text | None = None
    harness_type: Text | None = None
    status: Literal["registered", "active", "suspended", "retired"] | None = None
    invocation_surfaces: dict[str, Any] | None = None
    capabilities_ref: str | None = None


class HarnessMutation(Mutation):
    fields: HarnessFields


class ProjectFields(Request):
    application_scope: Scope | None = None
    name: Text | None = None
    repository_ref: Annotated[str, Field(pattern=r"^(platform|application:[A-Za-z][A-Za-z0-9_-]*)$")] | None = None
    parent_project_id: Identifier | None = None
    rank: int | None = None
    purpose: str | None = None
    target_outcome: str | None = None
    scope_note: str | None = None
    start_date: str | None = None
    target_date: str | None = None
    notes: str | None = None
    planning_estimate: dict[str, Any] | None = None

    @field_validator("planning_estimate")
    @classmethod
    def estimate_shape(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return validate_estimate(value)


class ProjectMutation(Mutation):
    kind: Literal["program", "project"] | None = None
    creation_authorization: Literal["not authorized"] | None = None
    fields: ProjectFields


class ProgramRevision(Mutation):
    target_outcome: Text
    done_tests: dict[str, list[dict[str, Any]]]
    scope_events: list[dict[str, Any]]
    change_days: dict[str, Any]
    as_of: Text
    pace: dict[str, Any]
    buffer_hours: dict[str, Any]
    goal_records: list[dict[str, Any]] | None = None


class ProjectAuthorizationChange(Mutation):
    authorization: Literal["authorized", "not authorized"]


class ProjectRetirement(Mutation):
    """Status-only retirement of one active program or execution project; nothing else can travel with it."""


class ProjectFormalLinkFields(Request):
    application_scope: Scope | None = None
    project_id: Identifier | None = None
    artifact_ref: Identifier | None = None
    status: Literal["active", "retired"] | None = None
    notes: str | None = None


class ProjectFormalLinkMutation(Mutation):
    fields: ProjectFormalLinkFields


class DependencyFields(Request):
    application_scope: Scope | None = None
    dependent_project_id: Identifier | None = None
    prerequisite_project_id: Identifier | None = None
    dependency_kind: Literal["requires_project_state"] | None = None
    required_prerequisite_state: Literal["active", "verified", "retired", "cancelled"] | None = None
    affected_gate: Literal["readiness", "closure"] | None = None
    rationale: Text | None = None
    provenance: Text | None = None
    related_work_item_id: Identifier | None = None
    status: Literal["active", "retired"] | None = None


class DependencyMutation(Mutation):
    fields: DependencyFields


class WorkItemFields(Request):
    application_scope: Scope | None = None
    title: Text | None = None
    description: str | None = None
    component: Text | None = None
    origin: Text | None = None
    source_spec_id: Identifier | None = None
    source_test_id: Identifier | None = None
    priority: Literal["P0", "P1", "P2", "P3"] | None = None
    acceptance_summary: str | None = None
    implementation_order: int | None = None
    depends_on_work_items: list[Identifier] | None = None
    status_detail: str | None = None
    resolution_status: Literal["open", "verified", "retired"] | None = None
    # Existing storage column: exact historical Git evidence or an explicit inspected false-completion clear.
    completion_evidence: str | None = None


class WorkItemMutation(Mutation):
    project_id: Identifier | None = None
    fields: WorkItemFields


class WorkItemRetirement(Mutation):
    """Status-only retirement of one open work item; nothing else can travel with it."""


class MembershipScopeFields(Request):
    application_scope: Scope


class MembershipScopeMutation(Mutation):
    expected_version: Annotated[int, Field(ge=1, lt=2_147_483_647)]
    fields: MembershipScopeFields


class MembershipMove(Mutation):
    source_project_id: Identifier
    destination_project_id: Identifier
    membership_order: int | None = None


DOMAINS = {
    "harnesses": "harnesses",
    "terms": "canonical_terms",
    "specifications": "specifications",
    "tests": "tests",
    "projects": "projects",
    "project-memberships": "project_work_item_memberships",
    "work-items": "work_items",
    "test-plans": "test_plans",
    "test-phases": "test_plan_phases",
    "project-dependencies": "project_dependencies",
    "project-formal-links": "project_artifact_links",
    # Historical reasoning records: readable, never amended through this service (SPEC-2098 v2).
    "deliberations": "deliberations",
}
FILTERS = {
    "harnesses": {"status"},
    "terms": {"scope", "authority_level", "lifecycle_status"},
    "specifications": {"status", "type", "priority", "authority", "testability", "application_scope", "scope"},
    "tests": {"test_type", "spec_id", "application_scope"},
    "projects": {"kind", "status", "parent_project_id", "repository_ref"},
    "project-memberships": {"status", "project_id", "work_item_id"},
    "work-items": {"resolution_status", "priority", "component", "source_spec_id"},
    "test-plans": {"status"},
    "test-phases": {"plan_id"},
    "project-dependencies": {"status", "dependent_project_id", "prerequisite_project_id", "affected_gate"},
    "project-formal-links": {"status", "project_id", "artifact_type"},
    "deliberations": {"source_type", "spec_id", "work_item_id"},
}
for _domain_filters in FILTERS.values():
    _domain_filters.add("application_scope")


def _error(code: str, message: str, **details: Any) -> NoReturn:
    raise PostgresKernelError(code, message, details=details)


def _record_scope(tx: PostgresTransaction, row: dict[str, Any], *, project_root: Path | None = None) -> str:
    """Read a direct classification; historical nulls never acquire a default."""
    scope = row.get("application_scope")
    try:
        validate_application_scope(project_root or tx.kernel.project_root, scope, require_explicit=True)
    except ApplicationRegistryError as error:
        _error("invalid_application_scope", str(error), id=row.get("id"), application_scope=scope)
    if not isinstance(scope, str):
        _error("invalid_application_scope", "A current operation requires an explicit record scope", id=row.get("id"))
    return scope


def _scope_matches(
    tx: PostgresTransaction,
    row: dict[str, Any],
    expected: str,
    *,
    project_root: Path | None = None,
    platform_governing: bool = False,
) -> None:
    actual = _record_scope(tx, row, project_root=project_root)
    if actual != expected and not (platform_governing and actual == "gtkb_platform"):
        _error(
            "application_scope_mismatch",
            "The explicit record scope does not match this workload relationship",
            id=row.get("id"),
            expected=expected,
            actual=actual,
        )


def _bound_session_binding(tx: PostgresTransaction, native_context_id: str) -> dict[str, Any]:
    """Read the actual initialized caller binding for one domain operation."""
    if not native_context_id.strip():
        _error("invalid_context_id", "Supply the actual native context identifier")
    tx.cursor.execute(
        sql.SQL("SELECT * FROM {}.session_init_bindings WHERE native_context_id=%s").format(sql.Identifier(tx.schema)),
        (native_context_id,),
    )
    binding = tx.cursor.fetchone()
    if binding is None:
        _error("no_session_binding", "The native context has no immutable binding; initialize that exact context")
    binding = normalize_bindings([dict(binding)])[0]
    if binding["session_context_id"] is None:
        _error(
            "session_not_initialized",
            "Execution enrollment supplies no session authority; initialize that exact native context",
        )
    return binding


def _assert_owner_operation(tx: PostgresTransaction, request: Mutation) -> None:
    """Keep owner ordering operations outside initialized PB/LO sessions.

    The ordinary administrative writer has no native context. Actor text is
    attribution; it cannot turn an initialized agent into the owner. This is
    process guidance at a domain boundary, not caller authentication.
    """
    if request.native_context_id is None:
        return
    binding = _bound_session_binding(tx, request.native_context_id)
    _error(
        "owner_operation_required",
        "The owner performs this operation outside a Prime Builder or Loyal Opposition session",
        role=binding["role"],
    )


def _bound_application_scope(
    tx: PostgresTransaction, native_context_id: str, *, project_root: Path | None = None
) -> str:
    """Consume the existing immutable subject, without creating a second context."""
    binding = _bound_session_binding(tx, native_context_id)
    subject = binding["subject"]
    if subject == "gtkb":
        return "gtkb_platform"
    role = {"prime-builder": "pb", "loyal-opposition": "lo"}.get(binding["role"])
    expected_identity = hashlib.sha256(f"::init application:{subject} {role}".encode("utf-8")).hexdigest()
    if role is None or binding["minimum_idempotency_identity"] != expected_identity:
        _error(
            "application_binding_unscoped",
            "The historical generic application binding cannot identify one application; "
            "initialize a fresh exact context",
        )
    return _record_scope(tx, {"application_scope": "application:" + subject}, project_root=project_root)


def _assert_request_scope(
    tx: PostgresTransaction,
    row: dict[str, Any],
    request: Mutation,
    *,
    project_root: Path | None = None,
) -> str:
    scope = _record_scope(tx, row, project_root=project_root)
    if request.native_context_id is not None:
        bound_scope = _bound_application_scope(tx, request.native_context_id, project_root=project_root)
        if scope != bound_scope:
            _error(
                "application_scope_mismatch",
                "The actual bound subject does not match the authored record scope",
                id=row.get("id"),
            )
    return scope


def _validate_scope_relationships(
    tx: PostgresTransaction,
    table: str,
    row: dict[str, Any],
    *,
    project_root: Path | None = None,
    allow_unresolved: bool = False,
    preserve_unresolved_inbound_records: bool = False,
    lock: bool = False,
) -> None:
    """Validate workload edges while preserving explicit governing/dependency semantics."""
    scope = _record_scope(tx, row, project_root=project_root)

    def same(target: dict[str, Any], *, governing: bool = False) -> None:
        if allow_unresolved and target.get("application_scope") is None:
            return
        _scope_matches(tx, target, scope, project_root=project_root, platform_governing=governing)

    def required(target_table: str, key: str) -> dict[str, Any]:
        return _required(tx, target_table, key, lock=lock)

    if table == "specifications":
        for key in (row.get("parent"), row.get("provisional_until"), *(row.get("affected_by") or [])):
            if key:
                same(required("specifications", key), governing=True)
        for test in _related(tx, "tests", spec_id=row["id"]):
            if preserve_unresolved_inbound_records and test.get("application_scope") is None:
                continue
            same(test)
        for work in _related(tx, "work_items", source_spec_id=row["id"]):
            if preserve_unresolved_inbound_records and work.get("application_scope") is None:
                continue
            same(work)
        for link in _related(
            tx, "project_artifact_links", artifact_ref=row["id"], artifact_type="spec", status="active"
        ):
            owner = required("projects", link["project_id"])
            if allow_unresolved and owner.get("application_scope") is None:
                if link.get("application_scope") is None:
                    continue
                owner_scope = _record_scope(tx, link, project_root=project_root)
            else:
                owner_scope = _record_scope(tx, owner, project_root=project_root)
                if not (allow_unresolved and link.get("application_scope") is None):
                    _scope_matches(tx, link, owner_scope, project_root=project_root)
            if scope != owner_scope and not (scope == "gtkb_platform" and link.get("relationship") == "governed_by"):
                _error(
                    "application_scope_mismatch", "A governing formal cannot become a foreign workload", id=row["id"]
                )
    elif table == "tests":
        if row.get("spec_id"):
            same(required("specifications", row["spec_id"]))
        for work in _related(tx, "work_items", source_test_id=row["id"]):
            if allow_unresolved:
                work = required("work_items", work["id"])
                if work.get("source_test_id") != row["id"]:
                    _error("work_evidence_mismatch", "The retained work item no longer names this test", id=work["id"])
            same(work)
            if row.get("spec_id") != work.get("source_spec_id"):
                # Only an existing NULL-scope Test's exact scope-only annotation reaches this branch.
                # Its legacy work/source gaps remain visible; current evidence admission stays strict.
                if not (allow_unresolved and work.get("application_scope") is None and row.get("spec_id")):
                    _error(
                        "work_evidence_mismatch",
                        "The work item and its test must name the same source specification",
                        id=work["id"],
                    )
                if work.get("source_spec_id"):
                    same(required("specifications", work["source_spec_id"]))
        for phase in _related(tx, "test_plan_phases"):
            if row["id"] in (phase.get("test_ids") or []):
                same(phase)
    elif table == "test_plans":
        for phase in _related(tx, "test_plan_phases", plan_id=row["id"]):
            same(phase)
    elif table == "test_plan_phases":
        if row.get("plan_id"):
            same(required("test_plans", row["plan_id"]))
        for key in row.get("test_ids") or []:
            same(required("tests", key))
    elif table == "projects":
        if row.get("kind") == "project":
            reference = row.get("repository_ref")
            expected = "platform" if scope == "gtkb_platform" else scope
            if reference is not None and reference != expected:
                _error(
                    "application_scope_mismatch",
                    "Explicit project scope and Git storage identity disagree",
                    id=row["id"],
                )
        if row.get("parent_project_id"):
            same(required("projects", row["parent_project_id"]), governing=True)
        for member in _related(tx, "project_work_item_memberships", project_id=row["id"], status="active"):
            same(member)
            same(required("work_items", member["work_item_id"]))
        for link in _related(tx, "project_artifact_links", project_id=row["id"], status="active"):
            same(link)
        for edge in _related(tx, "project_dependencies", dependent_project_id=row["id"], status="active"):
            same(edge)
    elif table == "work_items":
        for field, target_table in (("source_spec_id", "specifications"), ("source_test_id", "tests")):
            if row.get(field):
                same(required(target_table, row[field]))
        if row.get("source_test_id"):
            test = required("tests", row["source_test_id"])
            if test.get("spec_id") != row.get("source_spec_id"):
                _error(
                    "work_evidence_mismatch",
                    "The work item and its test must name the same source specification",
                    id=row["id"],
                )
        for member in _related(tx, "project_work_item_memberships", work_item_id=row["id"], status="active"):
            same(member)
            same(required("projects", member["project_id"]))
    elif table == "project_work_item_memberships":
        same(required("projects", row["project_id"]))
        same(required("work_items", row["work_item_id"]))
    elif table == "project_artifact_links":
        same(required("projects", row["project_id"]))
        if row.get("status") == "active" and row.get("artifact_type") == "spec":
            same(
                required("specifications", row["artifact_ref"]),
                governing=row.get("relationship") == "governed_by",
            )
    elif table == "project_dependencies":
        same(required("projects", row["dependent_project_id"]))
        # A current explicit dependency is the relationship authority; scopes
        # identify its endpoints but need not be identical across applications.
        prerequisite = required("projects", row["prerequisite_project_id"])
        prerequisite_scope = prerequisite.get("application_scope")
        if not (allow_unresolved and prerequisite_scope is None):
            prerequisite_scope = _record_scope(tx, prerequisite, project_root=project_root)
        if row.get("related_work_item_id"):
            related = required("work_items", row["related_work_item_id"])
            if allow_unresolved and related.get("application_scope") is None:
                return
            related_scope = _record_scope(tx, related, project_root=project_root)
            if related_scope not in {scope, prerequisite_scope}:
                _error(
                    "application_scope_mismatch", "Related work belongs to neither dependency endpoint", id=row["id"]
                )


def _validate_work_dependency_scopes(
    tx: PostgresTransaction,
    work: dict[str, Any],
    project_id: str,
    *,
    project_root: Path | None = None,
    lock: bool = False,
) -> None:
    scope = _record_scope(tx, work, project_root=project_root)
    for key in work.get("depends_on_work_items") or []:
        predecessor = _required(tx, "work_items", key, lock=lock)
        if _record_scope(tx, predecessor, project_root=project_root) == scope:
            continue
        parent = _current_parent(tx, key)
        edges = _related(tx, "project_dependencies", dependent_project_id=project_id, status="active")
        matching = [edge for edge in edges if edge["prerequisite_project_id"] == parent["project_id"]]
        if not matching:
            _error(
                "application_scope_mismatch",
                "A foreign work predecessor requires the explicit current project dependency",
                id=key,
            )
        for edge in matching:
            _validate_scope_relationships(tx, "project_dependencies", edge, project_root=project_root, lock=lock)


def _validate_work_scope_correction(tx: PostgresTransaction, work: dict[str, Any]) -> None:
    """Apply the existing structural scope contract to this work item's incident edges.

    Neighbors retain their actual scopes; only incident relationship fields enter
    this validation view. No unrelated row is reclassified or written. The caller's
    serializable transaction covers inbound scans as well as the locked endpoints.
    """
    tables: dict[str, list[dict[str, Any]]] = {table: [] for table in CURRENT_TABLES}
    records: dict[tuple[str, str], dict[str, Any]] = {}

    def retain(table: str, record: dict[str, Any], **edges: Any) -> dict[str, Any]:
        key = table, record["id"]
        if key not in records:
            records[key] = {"id": record["id"], "application_scope": record.get("application_scope")}
            tables[table].append(records[key])
        records[key].update(edges)
        return records[key]

    def endpoint(table: str, key: str) -> dict[str, Any]:
        record = _required(tx, table, key, lock=True)
        retain(table, record)
        return record

    retain("work_items", work, **{key: value for key, value in work.items() if key != "id"})
    for field, table in (("source_spec_id", "specifications"), ("source_test_id", "tests")):
        if work.get(field):
            record = endpoint(table, work[field])
            if table == "tests":
                retain(table, record, spec_id=record.get("spec_id"))
    for field, table in (
        ("related_spec_ids_at_creation", "specifications"),
        ("related_deliberation_ids", "deliberations"),
        ("supersedes", "work_items"),
        ("superseded_by", "work_items"),
        ("depends_on_work_items", "work_items"),
    ):
        for key in work.get(field) or []:
            endpoint(table, key)

    dependency_work = {work["id"], *(work.get("depends_on_work_items") or [])}
    for candidate in _related(tx, "work_items"):
        if candidate["id"] == work["id"]:
            continue
        inbound = {
            field: [work["id"]]
            for field in ("supersedes", "superseded_by", "depends_on_work_items")
            if work["id"] in (candidate.get(field) or [])
        }
        if inbound:
            candidate = _required(tx, "work_items", candidate["id"], lock=True)
            inbound = {field: value for field, value in inbound.items() if work["id"] in (candidate.get(field) or [])}
            retain("work_items", candidate, **inbound)
            if "depends_on_work_items" in inbound:
                dependency_work.add(candidate["id"])

    parents: dict[str, list[str]] = {}
    for key in sorted(dependency_work):
        # Every target membership is incident; unchanged neighbors contribute
        # only their active parent authority, never removed membership history.
        status = {} if key == work["id"] else {"status": "active"}
        for member in _related(tx, "project_work_item_memberships", work_item_id=key, **status):
            member = _required(tx, "project_work_item_memberships", member["id"], lock=True)
            tables["project_work_item_memberships"].append(member)
            endpoint("projects", member["project_id"])
            if member["status"] == "active":
                parents.setdefault(key, []).append(member["project_id"])
    # Only explicit foreign predecessor pairs need project-edge authority.
    # Keep all active parents above so ambiguity is refused by the validator.
    pairs = {
        (parents[row["id"]][0], parents[key][0])
        for row in tables["work_items"]
        for key in row.get("depends_on_work_items") or []
        if row.get("application_scope") is not None
        and records["work_items", key].get("application_scope") is not None
        and row["application_scope"] != records["work_items", key]["application_scope"]
        and len(parents.get(row["id"], [])) == len(parents.get(key, [])) == 1
    }
    dependencies = {
        edge["id"]: edge
        for dependent, prerequisite in sorted(pairs)
        for edge in _related(
            tx,
            "project_dependencies",
            dependent_project_id=dependent,
            prerequisite_project_id=prerequisite,
            status="active",
        )
    }
    dependencies.update(
        {edge["id"]: edge for edge in _related(tx, "project_dependencies", related_work_item_id=work["id"])}
    )
    for edge in dependencies.values():
        edge = _required(tx, "project_dependencies", edge["id"], lock=True)
        for field in ("dependent_project_id", "prerequisite_project_id"):
            endpoint("projects", edge[field])
        retain(
            "project_dependencies",
            edge,
            dependent_project_id=edge["dependent_project_id"],
            prerequisite_project_id=edge["prerequisite_project_id"],
            status=edge["status"],
            related_work_item_id=work["id"] if edge.get("related_work_item_id") == work["id"] else None,
        )
    for record in _related(tx, "deliberations", work_item_id=work["id"]):
        record = _required(tx, "deliberations", record["id"], lock=True)
        retain("deliberations", record, work_item_id=record.get("work_item_id"))
    tx.cursor.execute(
        sql.SQL("SELECT * FROM {}.deliberation_work_items WHERE work_item_id=%s FOR UPDATE").format(
            sql.Identifier(tx.schema)
        ),
        (work["id"],),
    )
    for raw in tx.cursor.fetchall():
        link = dict(raw)
        endpoint("deliberations", link["deliberation_id"])
        tables["deliberation_work_items"].append(link)
    validate_application_scope_relationships(tables)


def _refuse_role_bearing_surfaces(record_id: str, surfaces: dict[str, Any]) -> None:
    """Refuse invocation surfaces that name a role or a placeholder outside the contract (c123; batch design WP2 2.1).

    Roles bind to contexts only through the received ``::init`` marker; a registration that carried one in a dispatch
    tag, a role key or a ``--skill`` argument let a launch argument stand in for the binding. Nothing is written when
    this refuses.
    """
    from ._harness_surfaces import PLACEHOLDERS, ROLE_FINDINGS, surface_findings

    findings = surface_findings(surfaces)
    if not findings:
        return
    listed = [{"code": finding.code, "path": finding.path, "value": finding.value} for finding in findings]
    if any(finding.code in ROLE_FINDINGS for finding in findings):
        _error(
            "harness_surface_names_role",
            "A harness registration names no role: a role binds to a context only through the ::init marker that "
            "context receives, never through a registered dispatch tag, role key, argv init marker or --skill argument",
            id=record_id,
            findings=listed,
        )
    _error(
        "unknown_invocation_placeholder",
        f"A harness invocation uses only the contract's placeholders: {', '.join(sorted(PLACEHOLDERS))}",
        id=record_id,
        findings=listed,
    )


def _required(tx: PostgresTransaction, table: str, record_id: str, *, lock: bool = False) -> dict[str, Any]:
    record = tx.get(table, {"id": record_id}, lock=lock)
    if record is None:
        _error("not_found", "Canonical record does not exist", domain=table, id=record_id)
    return record


def _related(tx: PostgresTransaction, table: str, **filters: Any) -> list[dict[str, Any]]:
    """Read all matching relationships without silently truncating a closure."""
    result: list[dict[str, Any]] = []
    after = None
    while True:
        page = tx.list(table, filters=filters, after=after, limit=tx.kernel._operation_policy().read_page_size)
        result.extend(page)
        if len(page) < tx.kernel._operation_policy().read_page_size:
            return result
        after = page[-1]["id"]


def _planning_call(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except PlanningError as error:
        _error(error.code, "The program planning request is invalid or its canonical basis changed")


def _estimate_evidence(
    tx: PostgresTransaction,
    estimate: dict[str, Any] | None,
    scope: str,
    *,
    project_root: Path | None,
    bases: list[dict[str, Any]] | None = None,
    lock: bool = True,
) -> dict[tuple[str, str], dict[str, Any]]:
    evidence: dict[tuple[str, str], dict[str, Any]] = {}
    bases = estimate_bases(estimate) if bases is None else bases
    for basis in bases:
        for reference in basis["evidence_refs"]:
            key = reference["domain"], reference["id"]
            if key in evidence:
                continue
            if key[0] == "bridge-attempts":
                if reference["version"] is not None:
                    _error("invalid_planning_version", "Bridge attempts have no row version")
                tx.cursor.execute(
                    sql.SQL("SELECT * FROM {}.bridge_attempts WHERE id=%s" + (" FOR UPDATE" if lock else "")).format(
                        sql.Identifier(tx.schema)
                    ),
                    (key[1],),
                )
                raw = tx.cursor.fetchone()
                if raw is None or not raw["project_id"]:
                    _error("estimate_evidence_changed", "The named bridge attempt has no current scoped project")
                row = dict(raw)
                owner = _required(tx, "projects", row["project_id"], lock=lock)
                _scope_matches(tx, owner, scope, project_root=project_root)
            else:
                row = _required(tx, {"projects": "projects", "work-items": "work_items"}[key[0]], key[1], lock=lock)
                _scope_matches(tx, row, scope, project_root=project_root)
            evidence[key] = row
    return evidence


def _goal_record_inputs(tx, program, refs, *, project_root, lock=False, links=None):
    records = {}
    scope = _record_scope(tx, program, project_root=project_root)
    for ref in _planning_call(goal_references, refs):
        table = {"tests": "tests", "work-items": "work_items", "projects": "projects"}[ref["domain"]]
        row = tx.get(table, {"id": ref["id"]}, lock=lock)
        if row is not None:
            _scope_matches(tx, row, scope, project_root=project_root)
            records[ref["domain"], ref["id"]] = row
            if ref["domain"] == "projects" and links is not None:
                observed_ids = {link["id"] for link in links}
                for link in _related(
                    tx,
                    "project_artifact_links",
                    project_id=row["id"],
                    status="active",
                    artifact_type="git_commit",
                    relationship="activation",
                ):
                    if lock:
                        link = _required(tx, "project_artifact_links", link["id"], lock=True)
                    _scope_matches(tx, link, scope, project_root=project_root)
                    if link["id"] not in observed_ids:
                        links.append(link)
                        observed_ids.add(link["id"])
    return records


def _program_inputs(
    tx: PostgresTransaction,
    record_id: str,
    *,
    native_context_id: str | None,
    project_root: Path | None,
    lock: bool = False,
) -> tuple[
    dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]
]:
    """Read the complete current scoped plan from existing rows in one transaction."""
    program = _required(tx, "projects", record_id, lock=lock)
    if program["kind"] != "program":
        _error("program_required", "Program planning requires a program record")
    scope = _record_scope(tx, program, project_root=project_root)
    if (
        native_context_id is not None
        and _bound_application_scope(tx, native_context_id, project_root=project_root) != scope
    ):
        _error("application_scope_mismatch", "The initialized subject does not match this program")

    def checked(table: str, row: dict[str, Any], *, governing: bool = False) -> dict[str, Any]:
        current = _required(tx, table, row["id"], lock=lock) if lock else row
        _scope_matches(tx, current, scope, project_root=project_root, platform_governing=governing)
        return current

    children = [
        checked("projects", row)
        for row in _related(tx, "projects", parent_project_id=record_id)
        if row["status"] in {"active", "verified"}
    ]
    if any(row["kind"] != "project" for row in children):
        _error("invalid_program_parent", "A program's planning units must be execution projects")
    dependencies, links, tests, specifications = [], [], {}, {}
    pending: list[tuple[str, bool]] = []
    expanded: set[str] = set()
    for child in children:
        for row in _related(tx, "project_dependencies", dependent_project_id=child["id"], status="active"):
            row = checked("project_dependencies", row)
            _validate_scope_relationships(tx, "project_dependencies", row, project_root=project_root, lock=lock)
            dependencies.append(row)
        for row in _related(tx, "project_artifact_links", project_id=child["id"], status="active"):
            row = checked("project_artifact_links", row)
            links.append(row)
            if row["artifact_type"] == "spec":
                pending.append((row["artifact_ref"], True))
    # Actual formal descendants and their ancestor rows, not an authored list of
    # fields or source-version assertions, supply done-test and freshness inputs.
    while pending:
        key, descend = pending.pop()
        if key not in specifications:
            row = checked("specifications", _required(tx, "specifications", key), governing=True)
            specifications[key] = row
            if row.get("parent"):
                pending.append((row["parent"], False))
        if not descend or key in expanded:
            continue
        expanded.add(key)
        for descendant in _related(tx, "specifications", parent=key):
            pending.append((descendant["id"], True))
        for test in _related(tx, "tests", spec_id=key):
            if test.get("application_scope") == scope:
                tests[test["id"]] = checked("tests", test)
    # Include every individually stored snapshot source for a truthful current
    # version comparison, even when a current formal relationship was removed.
    plan = _planning_call(read_plan, program.get("notes"))
    if plan is not None:
        families = plan.get("source_versions", {})
        for family, table, target in (("tests", "tests", tests), ("specifications", "specifications", specifications)):
            for key in families.get(family, {}):
                if key not in target:
                    row = tx.get(table, {"id": key}, lock=lock)
                    if row is not None:
                        target[key] = checked(table, row, governing=family == "specifications")
    return program, children, dependencies, links, tests, specifications


def _planning_history(tx: PostgresTransaction, record_id: str) -> list[dict[str, Any]]:
    revisions = []
    for row in tx.history("projects", {"id": record_id}):
        state = row["new_state"]
        if state.get("id") != record_id or state.get("kind") != "program":
            continue
        plan = _planning_call(read_plan, state.get("notes"))
        if plan is not None and plan["program_version"] == state["version"]:
            revisions.append(state)
    return sorted(revisions, key=lambda state: state["version"])


def _current_parent(tx: PostgresTransaction, work_item_id: str) -> dict[str, Any]:
    memberships = _related(tx, "project_work_item_memberships", work_item_id=work_item_id, status="active")
    if len(memberships) != 1:
        _error("invalid_membership", "A work item requires exactly one current project", work_item_id=work_item_id)
    return memberships[0]


def _membership_facts(tx: PostgresTransaction, work: dict[str, Any]) -> dict[str, Any]:
    """Recorded membership facts for a read.

    An open work item has exactly one active parent project; reading an open item with any other
    history is refused like every mutation, move and readiness check. Closed work keeps its recorded
    membership rows exactly as history (the migration preserves zero or several active memberships
    on the owner's decision), so a read returns those rows and ``membership`` only when exactly one is
    active. Nothing is repaired or invented on the read path.
    """
    memberships = _related(tx, "project_work_item_memberships", work_item_id=work["id"])
    active = [row for row in memberships if row["status"] == "active"]
    if work["resolution_status"] == "open":
        if len(active) != 1:
            _error("invalid_membership", "A work item requires exactly one current project", work_item_id=work["id"])
        return {"membership": active[0], "memberships": memberships}
    return {"membership": active[0] if len(active) == 1 else None, "memberships": memberships}


def _work_formal_roots(
    tx: PostgresTransaction, work: dict[str, Any], project_id: str, *, lock: bool = False
) -> dict[str, Any]:
    """Read canonical relationship inputs independently of supplementary citations."""
    work_sources = set(work.get("related_spec_ids_at_creation") or [])
    if work.get("source_spec_id"):
        work_sources.add(work["source_spec_id"])
    test_id = work.get("source_test_id")
    return {
        "work": sorted(work_sources),
        "test": {test_id: _required(tx, "tests", test_id, lock=lock)["spec_id"]} if test_id else {},
        "project": {
            link["id"]: link["artifact_ref"]
            for link in _related(tx, "project_artifact_links", project_id=project_id, status="active")
            if link["artifact_type"] == "spec"
        },
    }


def _work_formal_sources(
    tx: PostgresTransaction,
    work: dict[str, Any],
    project_id: str,
    *,
    additional_ids: list[str] | None = None,
    lock: bool = False,
    roots: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Read the declared formal closure in the caller's domain transaction.

    This is the explicit relationship floor, not a claim of complete semantic
    applicability. Context loading and bridge effects use the same facts.
    Cyclic cross-references are visited once, without dropping their records.
    """
    roots = roots if roots is not None else _work_formal_roots(tx, work, project_id, lock=lock)
    pending = (
        set(additional_ids or []) | set(roots["work"]) | set(roots["test"].values()) | set(roots["project"].values())
    )
    records: dict[str, dict[str, Any]] = {}
    while pending:
        for key in sorted(pending):
            record = tx.get("specifications", {"id": key}, lock=lock)
            if record is None:
                _error(
                    "not_found",
                    "A required formal source is missing; reconcile its canonical relationship",
                    domain="specifications",
                    id=key,
                    recovery_route=f"gt context work-item {work['id']}",
                )
            records[key] = record
        pending = {
            reference
            for record in records.values()
            for reference in [record.get("parent"), record.get("provisional_until"), *(record.get("affected_by") or [])]
            if reference and reference not in records
        }
    return [records[key] for key in sorted(records)]


def _test_phases(tx: PostgresTransaction, test_id: str) -> list[dict[str, Any]]:
    tx.cursor.execute(
        sql.SQL(
            "SELECT phase.id FROM {}.test_plan_phases phase JOIN {}.test_plans plan ON plan.id=phase.plan_id "
            "WHERE phase.test_ids @> jsonb_build_array(%s::text) AND plan.status='active' "
            "ORDER BY phase.plan_id,phase.phase_order,phase.id"
        ).format(sql.Identifier(tx.schema), sql.Identifier(tx.schema)),
        (test_id,),
    )
    return [_required(tx, "test_plan_phases", row["id"]) for row in tx.cursor.fetchall()]


def _work_evidence(
    tx: PostgresTransaction, state: dict[str, Any], *, project_root: Path | None = None, lock: bool = False
) -> None:
    """Require current evidence at creation, link replacement and proposal publication."""
    for field, table in (("source_spec_id", "specifications"), ("source_test_id", "tests")):
        if not state.get(field):
            _error("work_evidence_required", "Implementation work requires a specification and executable test")
        evidence = _required(tx, table, state[field])
        if table == "specifications" and evidence["status"] in {"retired", "superseded"}:
            _error("inactive_evidence", "Work must refer to current evidence", id=evidence["id"])
        if table == "tests" and not evidence.get("test_file"):
            _error("executable_test_required", "The linked test must identify executable work")
    scope = _record_scope(tx, state, project_root=project_root)
    source = _required(tx, "specifications", state["source_spec_id"], lock=lock)
    test = _required(tx, "tests", state["source_test_id"], lock=lock)
    _scope_matches(tx, source, scope, project_root=project_root)
    _scope_matches(tx, test, scope, project_root=project_root)
    if test.get("spec_id") != source["id"]:
        _error("work_evidence_mismatch", "The work item and its test must name the same source specification")
    phases = _test_phases(tx, state["source_test_id"])
    if not phases:
        _error("test_phase_required", "The executable test must belong to an active test plan phase")
    for phase in phases:
        _scope_matches(tx, phase, scope, project_root=project_root)
        _scope_matches(tx, _required(tx, "test_plans", phase["plan_id"], lock=lock), scope, project_root=project_root)


def _verification_evidence(tx: PostgresTransaction, spec_id: str) -> list[str]:
    """Executable tests of one specification that sit in a phase of an active test plan.

    The `_work_evidence` rule mirrored for implementation verification (owner ruling D17, WI-7861):
    a test counts when its ``spec_id`` is this specification, it identifies executable work
    (``test_file``) and an active plan phase lists it.
    """
    return [
        test["id"]
        for test in _related(tx, "tests", spec_id=spec_id)
        if test.get("test_file") and _test_phases(tx, test["id"])
    ]


def _execution_project(tx: PostgresTransaction, project_id: str) -> dict[str, Any]:
    project = _required(tx, "projects", project_id, lock=True)
    if project["kind"] != "project":
        _error("program_cannot_contain_work", "Programs sequence projects and cannot contain work items")
    if project["status"] != "active":
        _error("project_closed", "Membership cannot change in a closed project")
    return project


def _open_dependants(tx: PostgresTransaction, work_item_id: str) -> list[str]:
    """Open work items whose ``depends_on_work_items`` names this one, in code-point id order.

    ``depends_on_work_items`` is the sole writable predecessor list (DCL-STANDING-BACKLOG-DB-SCHEMA-001);
    ``blocks_work_items`` is imported legacy data with no native writer, reader or derived view and is not
    consulted. Closed dependants (any resolution other than ``open``) never block, and a legacy non-array
    JSONB value simply does not match.
    """
    tx.cursor.execute(
        sql.SQL(
            "SELECT id FROM {}.work_items WHERE id<>%s AND resolution_status='open' "
            "AND depends_on_work_items @> jsonb_build_array(%s::text)"
        ).format(sql.Identifier(tx.schema)),
        (work_item_id, work_item_id),
    )
    return sorted(row["id"] for row in tx.cursor.fetchall())


def _active_attempts(
    tx: PostgresTransaction,
    *,
    work_item_id: str | None = None,
    project_id: str | None = None,
    reviewed_only: bool = False,
) -> list[str]:
    """Identifiers of the active bridge attempts on one work item or on one project, in code-point id order.

    The bridge's own predicate (``disposition='active'``). A VERIFIED member keeps its attempt active until
    the project commit, so an active attempt marks reviewed-but-uncommitted work as well as work in progress.
    Ordinary planning/membership mutations filter to reviewed heads, whose work remains frozen.
    """
    named = {
        column: value
        for column, value in (("work_item_id", work_item_id), ("project_id", project_id))
        if value is not None
    }
    if len(named) != 1:
        _error("invalid_query", "Active attempts are selected by exactly one work item or one project")
    ((column, value),) = named.items()
    tx.cursor.execute(
        sql.SQL("SELECT id FROM {}.bridge_attempts WHERE {}=%s AND disposition='active'").format(
            sql.Identifier(tx.schema), sql.Identifier(column)
        )
        + (sql.SQL(" AND head_status='VERIFIED'") if reviewed_only else sql.SQL("")),
        (value,),
    )
    return sorted(row["id"] for row in tx.cursor.fetchall())


def _open_members(tx: PostgresTransaction, project: dict[str, Any]) -> dict[str, list[str]]:
    """Members that keep a project or program open: open work with an active membership, active child projects.

    A closed member's active membership is its preserved current parent (GOV-WORK-ITEM-TERMINAL-STATE-001,
    GOV-STANDING-BACKLOG-001) and never blocks. A program has no memberships and an execution project has
    no children, so one of the two lists is empty by construction.
    """
    memberships = _related(tx, "project_work_item_memberships", project_id=project["id"], status="active")
    return {
        "work_item_ids": sorted(
            row["work_item_id"]
            for row in memberships
            if _required(tx, "work_items", row["work_item_id"])["resolution_status"] == "open"
        ),
        "project_ids": sorted(
            row["id"] for row in _related(tx, "projects", parent_project_id=project["id"], status="active")
        ),
    }


def _active_dependants(tx: PostgresTransaction, project_id: str) -> list[dict[str, Any]]:
    """Active dependencies of active projects that require this prerequisite to reach a state other than retired.

    The ``unreachable_dependency`` rule of amend_dependency seen from the prerequisite's side: a retired
    prerequisite can never reach ``active`` or ``verified``. A dependency that requires ``retired`` is met by
    the retirement, and a closed dependent project no longer waits.
    """
    return [
        row
        for row in _related(tx, "project_dependencies", prerequisite_project_id=project_id, status="active")
        if row["required_prerequisite_state"] != "retired"
        and _required(tx, "projects", row["dependent_project_id"])["status"] == "active"
    ]


def _project_commit(tx: PostgresTransaction, project_id: str) -> str | None:
    links = _related(
        tx,
        "project_artifact_links",
        project_id=project_id,
        artifact_type="git_commit",
        relationship="activation",
        status="active",
    )
    commit = links[0]["artifact_ref"] if len(links) == 1 else None
    return commit if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", commit) else None


def _project_dependency_readiness(
    tx: PostgresTransaction, project_id: str, gate: str, *, lock: bool = False
) -> dict[str, Any]:
    if gate not in {"readiness", "closure"}:
        _error("invalid_dependency_gate", "Dependencies affect readiness or project closure")
    project = _required(tx, "projects", project_id, lock=lock)
    if project["kind"] != "project":
        _error("program_has_no_readiness", "Readiness applies to the execution projects sequenced by a program")
    results = []
    for record in _related(tx, "project_dependencies", dependent_project_id=project_id, status="active"):
        if dependency_shape(record) and record["affected_gate"] != gate:
            continue
        prerequisite = tx.get("projects", {"id": record["prerequisite_project_id"]}, lock=lock)
        current = prerequisite["status"] if prerequisite else None
        reason = None
        if not dependency_shape(record):
            reason = "invalid_dependency_contract"
        elif not prerequisite or prerequisite["kind"] != "project":
            reason = "invalid_prerequisite_project"
        elif current != record["required_prerequisite_state"]:
            reason = "prerequisite_state_not_reached"
        elif current == "verified" and not _project_commit(tx, prerequisite["id"]):
            reason = "prerequisite_commit_missing"
        results.append(
            {
                "dependency_id": record["id"],
                "prerequisite_project_id": record["prerequisite_project_id"],
                "required_state": record["required_prerequisite_state"],
                "current_state": current,
                "satisfied": reason is None,
                "reason": reason,
            }
        )
    return {
        "project_id": project_id,
        "gate": gate,
        "ready": all(row["satisfied"] for row in results),
        "dependencies": results,
    }


def _require_project_dependencies(
    tx: PostgresTransaction, project_id: str, gate: str = "readiness", *, lock: bool = False
) -> None:
    result = _project_dependency_readiness(tx, project_id, gate, lock=lock)
    if not result["ready"]:
        _error(
            "project_dependencies_unsatisfied",
            "Required predecessor results are not available; read project readiness",
            **result,
        )


def _write(
    tx: PostgresTransaction,
    table: str,
    record_id: str,
    fields: dict[str, Any],
    request: Mutation,
    *,
    defaults: dict[str, Any] | None = None,
    project_root: Path | None = None,
    authored_scope_only: bool = False,
    work_scope_correction: bool = False,
) -> dict[str, Any]:
    current = tx.get(table, {"id": record_id}, lock=True)
    actual = current["version"] if current else 0
    if actual != request.expected_version:
        _error(
            "cas_conflict",
            "Read the current record before applying this change",
            expected=request.expected_version,
            actual=actual,
            id=record_id,
        )
    state = dict(current) if current else {column: None for column in TABLE_SPECS[table].columns}
    if not current:
        state.update(defaults or {})
    state.update(fields)
    if table in APPLICATION_SCOPE_TABLES:
        _assert_request_scope(tx, {**state, "id": record_id}, request, project_root=project_root)
        correcting_work_scope = bool(
            work_scope_correction
            and table == "work_items"
            and current
            and current.get("application_scope") is not None
            and set(fields) == {"application_scope"}
        )
        _validate_scope_relationships(
            tx,
            table,
            {**state, "id": record_id},
            project_root=project_root,
            allow_unresolved=bool(
                current
                and current.get("application_scope") is None
                and (authored_scope_only or set(fields) == {"application_scope"})
            )
            or correcting_work_scope,
            preserve_unresolved_inbound_records=bool(
                table == "specifications"
                and current
                and current.get("application_scope") is not None
                and set(fields) <= {"description", "title", "assertions", "constraints", "implementation_verified_at"}
                and fields.get("implementation_verified_at") is None
            ),
            lock=True,
        )
        if correcting_work_scope:
            _validate_work_scope_correction(tx, {**state, "id": record_id})
    state.update(
        id=record_id,
        version=actual,
        changed_by=request.actor,
        changed_at=datetime.now(UTC).isoformat(),
        change_reason=request.reason,
    )
    result = tx.mutate(
        table=table,
        identity={"id": record_id},
        expected_version=actual,
        new_state=state,
        actor=request.actor,
        reason=request.reason,
    )
    record = result.get("record")
    if not isinstance(record, dict):
        _error("mutation_readback_mismatch", "Native mutation did not return a current record")
    return record


class AuthorityService:
    """Knowledge, planning, and task-context operations over native PostgreSQL."""

    def __init__(self, kernel: PostgresKernel, *, historical_commit_validator: HistoricalCommitValidator | None = None) -> None:
        self.kernel = kernel
        self._historical_commit_validator = historical_commit_validator
        kernel._claim_service_owner(self)

    def lifecycle_state(self) -> dict[str, Any]:
        return self.kernel.lifecycle_state()

    def close(self) -> None:
        self.kernel.close()

    def _validate_historical_commit(self, root: Path, project: dict[str, Any], evidence: str) -> str:
        if self._historical_commit_validator is None:
            raise PostgresKernelError('capability_unavailable', 'The required historical commit capability is unavailable', details={'capability':'historical_commit'})
        return self._historical_commit_validator(root, project, evidence)

    @operation_method
    def list_records(
        self,
        domain: str,
        *,
        filters: dict[str, Any] | None = None,
        after: str | None = None,
        limit: int,
        search: str | None = None,
    ) -> dict[str, Any]:
        if domain not in DOMAINS or set(filters or {}) - FILTERS[domain]:
            _error("invalid_query", "Unknown domain or unsupported filter")
        if domain == "project-formal-links":
            requested = (filters or {}).get("artifact_type", "spec")
            if requested not in {"spec", "bridge_thread", "completion_guard"}:
                _error(
                    "invalid_query",
                    "Formal links are listed by spec, bridge_thread or completion_guard",
                    artifact_type=requested,
                )
            filters = {**(filters or {}), "artifact_type": requested}
        with self.kernel.transaction(read_only=True) as tx:
            records = tx.list(DOMAINS[domain], filters=filters, after=after, limit=limit, search=search)
        return {"records": records, "next_after": records[-1]["id"] if len(records) == limit else None}

    @operation_method
    def show(self, domain: str, record_id: str) -> dict[str, Any]:
        if domain not in DOMAINS:
            _error("invalid_query", "Unknown knowledge domain")
        with self.kernel.transaction(read_only=True) as tx:
            row = _required(tx, DOMAINS[domain], record_id)
            if domain == "project-formal-links" and row["artifact_type"] not in {
                "spec",
                "bridge_thread",
                "completion_guard",
            }:
                _error(
                    "not_found", "The record is not a formal source or an obsolete project relationship", id=record_id
                )
            if domain == "projects":
                return {
                    "project": row,
                    "projects": _related(tx, "projects", parent_project_id=record_id),
                    "memberships": _related(tx, "project_work_item_memberships", project_id=record_id, status="active"),
                    "dependencies": _related(
                        tx, "project_dependencies", dependent_project_id=record_id, status="active"
                    ),
                    "artifact_links": _related(tx, "project_artifact_links", project_id=record_id, status="active"),
                }
            if domain == "work-items":
                return {"work_item": row, **_membership_facts(tx, row)}
            return row

    @operation_method
    def history(self, domain: str, record_id: str) -> dict[str, Any]:
        """The current record and its complete version chain; a missing record is not found, never empty history."""
        if domain not in DOMAINS:
            _error("invalid_query", "Unknown knowledge domain")
        with self.kernel.transaction(read_only=True) as tx:
            current = _required(tx, DOMAINS[domain], record_id)
            rows = tx.history(DOMAINS[domain], {"id": record_id})
        history = [
            {
                "version": row["new_version"],
                "prior_version": row["prior_version"],
                "actor": row["actor"],
                "changed_at": row["changed_at"],
                "reason": row["reason"],
                "state": row["new_state"],
            }
            for row in rows
        ]
        return {"current": current, "history": history}

    @operation_method
    def specification_snapshot(self) -> dict[str, Any]:
        """Read the whole active formal corpus in one repeatable-read transaction."""
        with self.kernel.transaction(read_only=True) as tx:
            records = _related(tx, "specifications", status="active")
        return {"records": records, "consistency": "single_read_transaction"}

    @staticmethod
    def _term_source_issue(tx: PostgresTransaction, record: dict[str, Any]) -> dict[str, Any] | None:
        source = tx.get("specifications", {"id": record["source_authority"]})
        if source is None or source["status"] != "active":
            return {
                "id": record["id"],
                "source_authority": record["source_authority"],
                "status": source["status"] if source else "missing",
            }
        return None

    @operation_method
    def resolve_authority(self, subject: str, *, scope: str | None = None) -> dict[str, Any]:
        from .terminology import AuthorityResolutionError, resolve_term

        with self.kernel.transaction(read_only=True) as tx:
            try:
                result = resolve_term(subject, records=_related(tx, "canonical_terms"), scope=scope)
            except AuthorityResolutionError as error:
                _error("invalid_terminology", str(error))
            if result["status"] == "resolved":
                issue = self._term_source_issue(tx, result["record"])
                if issue:
                    _error("invalid_term_source", "The term's formal source requires reconciliation", **issue)
            return result

    @operation_method
    def authority_status(self, *, scope: str | None = None) -> dict[str, Any]:
        from .terminology import compact_status

        with self.kernel.transaction(read_only=True) as tx:
            records = _related(tx, "canonical_terms", **({"scope": scope} if scope is not None else {}))
            result = compact_status(records=records, scope=scope)
            issues = [
                issue
                for row in records
                if row["lifecycle_status"] == "active"
                if (issue := self._term_source_issue(tx, row)) is not None
            ]
            result["source_issues"] = issues
            if issues:
                result["status"] = "fail"
            return result

    @operation_method
    def amend_term(self, record_id: str, request: TermMutation, *, project_root: Path | None = None) -> dict[str, Any]:
        from .terminology import compact_status

        with self.kernel.transaction() as tx:
            fields = request.fields.model_dump(exclude_unset=True)
            row = _write(
                tx,
                "canonical_terms",
                record_id,
                fields,
                request,
                project_root=project_root,
                defaults={"lifecycle_status": "candidate"},
            )
            validation = compact_status(records=[{**row, "lifecycle_status": "active"}])
            if validation["validation_issues"]:
                _error("invalid_terminology", "Term names require correction", issues=validation["validation_issues"])
            if row["lifecycle_status"] == "active":
                issue = self._term_source_issue(tx, row)
                if issue:
                    _error("invalid_term_source", "An active term requires a current formal source", **issue)
            return row

    @operation_method
    def registry_path_observations(self) -> list[dict[str, str]]:
        """Read the complete typed path inventory from one current snapshot."""
        with self.kernel.transaction(read_only=True) as tx:
            try:
                return registry_path_observations(
                    specifications=_related(tx, "specifications"),
                    tests=_related(tx, "tests"),
                    documents=_related(tx, "documents"),
                    project_artifact_links=_related(tx, "project_artifact_links", status="active"),
                )
            except ValueError as exc:
                _error("invalid_registry_path_source", str(exc))

    @operation_method
    def amend_harness(
        self, record_id: str, request: HarnessMutation, *, project_root: Path | None = None
    ) -> dict[str, Any]:
        """Create or amend one harness installation record; roles never live here (they bind to contexts)."""
        from ._arbitration import arbitrate
        from ._harness_lifecycle import STATUS_REGISTERED, validate_transition

        fields = request.fields.model_dump(exclude_unset=True)
        if fields.get("invocation_surfaces") is not None:
            _refuse_role_bearing_surfaces(record_id, fields["invocation_surfaces"])
        with self.kernel.transaction() as tx:
            arbitrate(tx)
            current = tx.get("harnesses", {"id": record_id}, lock=True)
            if current is None:
                for required in ("harness_name", "harness_type"):
                    if not fields.get(required):
                        _error(
                            "harness_fields_required", "A new harness needs harness_name and harness_type", id=record_id
                        )
                if fields.get("status") not in (None, STATUS_REGISTERED):
                    _error(
                        "invalid_harness_transition",
                        "A new harness starts as registered",
                        id=record_id,
                        status=fields["status"],
                    )
            elif "status" in fields and fields["status"] != current["status"]:
                try:
                    validate_transition(current["status"], fields["status"])
                except ValueError as exc:
                    _error("invalid_harness_transition", str(exc), id=record_id)
            return _write(
                tx,
                "harnesses",
                record_id,
                fields,
                request,
                project_root=project_root,
                defaults={"status": STATUS_REGISTERED},
            )

    @operation_method
    def amend_specification(
        self, record_id: str, request: SpecMutation, *, project_root: Path | None = None
    ) -> dict[str, Any]:
        """Create or amend one specification; ``retired_at`` and ``implementation_verified_at`` are service-stamped.

        ``implementation_verified_at: true`` asserts that the implementation is verified now. The service
        refuses it with ``verification_evidence_required`` unless at least one test of this specification
        has a ``test_file`` and sits in an active test-plan phase (the ``_work_evidence`` mirror, owner
        ruling D17); with evidence it stamps its own clock, so the response differs from the request by
        design. ``null`` clears the marker without evidence. R26 (``gt kb reconcile --provisionals``) reads
        the stamped column.
        """
        fields = request.fields.model_dump(exclude_unset=True)
        with self.kernel.transaction() as tx:
            current = tx.get("specifications", {"id": record_id}, lock=True)
            scope = fields.get("application_scope", current.get("application_scope") if current else None)
            try:
                validate_application_scope(project_root, scope)
            except ApplicationRegistryError as error:
                _error("invalid_application_scope", str(error), id=record_id, application_scope=scope)
            for reference in [
                fields.get("parent"),
                fields.get("provisional_until"),
                *(fields.get("affected_by") or []),
            ]:
                if reference:
                    _required(tx, "specifications", reference)
            # A retirement time is an observed event, not client-invented metadata.
            if fields.get("status") == "retired":
                fields["retired_at"] = datetime.now(UTC).isoformat()
            # So is a verification time: the request asserts it, the evidence rule admits it, the service stamps it.
            if fields.get("implementation_verified_at") is True:
                if not _verification_evidence(tx, record_id):
                    _error(
                        "verification_evidence_required",
                        "Implementation verification requires an executable test of this specification "
                        "in an active test plan phase",
                        id=record_id,
                    )
                fields["implementation_verified_at"] = datetime.now(UTC).isoformat()
            return _write(
                tx,
                "specifications",
                record_id,
                fields,
                request,
                project_root=project_root,
                defaults={"status": "active"},
            )

    @operation_method
    def amend_test(self, record_id: str, request: TestMutation, *, project_root: Path | None = None) -> dict[str, Any]:
        from ._arbitration import arbitrate

        with self.kernel.transaction() as tx:
            arbitrate(tx)
            fields = request.fields.model_dump(exclude_unset=True)
            authored_scope_only = set(fields) == {"application_scope"}
            current = tx.get("tests", {"id": record_id}, lock=True)
            # Execution belongs to the previous selector, scope and acceptance definition.
            # The existing kernel history preserves that prior row; authors cannot forge results.
            definition_fields = (
                "test_file",
                "test_class",
                "test_function",
                "test_type",
                "expected_outcome",
                "spec_id",
                "application_scope",
            )
            if current and any(key in fields and fields[key] != current.get(key) for key in definition_fields):
                fields.update(last_result=None, last_executed_at=None, last_executed_on=None, execution_evidence=None)
            scope = fields.get("application_scope", current.get("application_scope") if current else None)
            try:
                validate_application_scope(project_root, scope)
            except ApplicationRegistryError as error:
                _error("invalid_application_scope", str(error), id=record_id, application_scope=scope)
            return _write(
                tx,
                "tests",
                record_id,
                fields,
                request,
                project_root=project_root,
                authored_scope_only=authored_scope_only,
            )

    @operation_method
    def amend_test_plan(
        self, record_id: str, request: TestPlanMutation, *, project_root: Path | None = None
    ) -> dict[str, Any]:
        with self.kernel.transaction() as tx:
            return _write(
                tx,
                "test_plans",
                record_id,
                request.fields.model_dump(exclude_unset=True),
                request,
                project_root=project_root,
                defaults={"status": "active"},
            )

    @operation_method
    def amend_test_phase(
        self, record_id: str, request: TestPhaseMutation, *, project_root: Path | None = None
    ) -> dict[str, Any]:
        with self.kernel.transaction() as tx:
            fields = request.fields.model_dump(exclude_unset=True)
            authored_scope_only = set(fields) == {"application_scope"}
            current = tx.get("test_plan_phases", {"id": record_id}, lock=True)
            if current and (
                ("test_ids" in fields and set(fields["test_ids"] or []) != set(current["test_ids"] or []))
                or any(key in fields and fields[key] != current.get(key) for key in ("plan_id", "application_scope"))
            ):
                # Stored execution evidence belongs to the previous membership.
                # Native amendments cannot supply a replacement execution result.
                fields.update(last_result=None, last_executed_at=None, last_executed_on=None)
            for test_id in fields.get("test_ids") or []:
                _required(tx, "tests", test_id)
            return _write(
                tx,
                "test_plan_phases",
                record_id,
                fields,
                request,
                project_root=project_root,
                authored_scope_only=authored_scope_only,
            )

    @operation_method
    def amend_project(
        self, record_id: str, request: ProjectMutation, *, project_root: Path | None = None
    ) -> dict[str, Any]:
        fields = request.fields.model_dump(exclude_unset=True)
        with self.kernel.transaction() as tx:
            current = tx.get("projects", {"id": record_id}, lock=True)
            kind = request.kind or (current["kind"] if current else "project")
            planned_creation = request.creation_authorization is not None
            if planned_creation and (current is not None or request.expected_version != 0 or request.kind != "project"):
                _error("invalid_planning_creation", "Not-authorized planning creation requires a new explicit project")
            if current is None and kind == "project" and not planned_creation:
                _assert_owner_operation(tx, request)
            if current and current.get("application_scope") is None and set(fields) == {"application_scope"}:
                if kind != current["kind"]:
                    _error("project_structure_frozen", "Scope metadata reconciliation cannot change project kind")
                return _write(tx, "projects", record_id, fields, request, project_root=project_root)
            if current and (kind != current["kind"] or current["status"] != "active"):
                _error("project_structure_frozen", "Kind changes and closed project changes require reconciliation")
            parent = fields.get("parent_project_id", current["parent_project_id"] if current else None)
            if parent:
                program = _required(tx, "projects", parent, lock=True)
                if kind != "project" or program["kind"] != "program" or program["status"] != "active":
                    _error("invalid_program_parent", "An execution project may have one active program parent")
            if planned_creation:
                if not parent:
                    _error("invalid_program_parent", "A planning child requires its current active program parent")
                if request.native_context_id is None:
                    _error("no_session_binding", "Planning-child creation requires the initialized native context")
                scope = _assert_request_scope(tx, {**fields, "id": record_id}, request, project_root=project_root)
                _scope_matches(tx, program, scope, project_root=project_root)
            if "planning_estimate" in fields:
                scope = _assert_request_scope(
                    tx, {**(current or {}), **fields, "id": record_id}, request, project_root=project_root
                )
                fields["planning_estimate"] = _planning_call(
                    validate_estimate,
                    fields["planning_estimate"],
                    evidence=_estimate_evidence(tx, fields["planning_estimate"], scope, project_root=project_root),
                )
            repository_ref = fields.get("repository_ref", current.get("repository_ref") if current else None)
            if kind == "program":
                if repository_ref is not None:
                    _error("program_has_no_repository", "Programs sequence outcomes and do not select a Git repository")
            else:
                if repository_ref is None:
                    _error("project_repository_required", "Set the execution project's explicit repository_ref")
                if project_root is None:
                    _error(
                        "repository_host_required",
                        "Project repository validation requires the configured platform host",
                    )
                try:
                    resolve_project_repository(project_root, repository_ref, probe_seconds=self.kernel._operation_policy().repository_probe_seconds, deadline=self.kernel._operation_policy().deadline, checkpoint=self.kernel._operation_policy().checkpoint)
                except ApplicationRegistryError as error:
                    _error("invalid_repository_ref", str(error), repository_ref=repository_ref)
                if current and repository_ref != current.get("repository_ref"):
                    tx.cursor.execute(
                        sql.SQL(
                            "SELECT 1 FROM {}.bridge_attempts WHERE project_id=%s AND disposition='active' LIMIT 1"
                        ).format(sql.Identifier(tx.schema)),
                        (record_id,),
                    )
                    if tx.cursor.fetchone() or _project_commit(tx, record_id):
                        _error(
                            "project_repository_frozen",
                            "Repository reassignment cannot move active attempts or committed work",
                        )
            fields["kind"] = kind
            defaults = {"status": "active", "authorization": None if kind == "program" else "authorized"}
            if planned_creation:
                defaults["authorization"] = "not authorized"
            if record_id == "PROJECT-GTKB-NEW-WORK-INTAKE":
                defaults["authorization"] = "not authorized"
            return _write(tx, "projects", record_id, fields, request, project_root=project_root, defaults=defaults)

    @operation_method
    def revise_program(
        self, record_id: str, request: ProgramRevision, *, project_root: Path | None = None
    ) -> dict[str, Any]:
        with self.kernel.transaction() as tx:
            program, children, dependencies, links, tests, specs = _program_inputs(
                tx, record_id, native_context_id=request.native_context_id, project_root=project_root, lock=True
            )
            if program["version"] != request.expected_version:
                _error("cas_conflict", "Read the current program before revising its plan", id=record_id)
            plan = _planning_call(read_plan, program.get("notes"))
            refs = request.goal_records if request.goal_records is not None else (plan or {}).get("goal_records", [])
            observations = _goal_record_inputs(tx, program, refs, project_root=project_root, lock=True)
            evidence = _estimate_evidence(
                tx,
                None,
                _record_scope(tx, program, project_root=project_root),
                project_root=project_root,
                bases=_planning_call(pace_bases, request.pace)
                + [
                    basis
                    for child in children
                    for basis in _planning_call(estimate_bases, child.get("planning_estimate"))
                ],
            )
            fields = _planning_call(
                revision_fields,
                program,
                children,
                dependencies,
                links,
                tests,
                specs,
                goal_record_observations=observations,
                pace_evidence=evidence,
                estimate_evidence=evidence,
                **request.model_dump(exclude={"actor", "reason", "native_context_id"}),
            )
            return _write(tx, "projects", record_id, fields, request, project_root=project_root)

    @operation_method
    def program_checkpoint(
        self, record_id: str, *, as_of: str, native_context_id: str | None = None, project_root: Path | None = None
    ) -> dict[str, Any]:
        with self.kernel.transaction(read_only=True) as tx:
            program, children, dependencies, links, tests, specs = _program_inputs(
                tx, record_id, native_context_id=native_context_id, project_root=project_root
            )
            revisions = _planning_history(tx, record_id)
            plan = _planning_call(read_plan, program.get("notes"))
            observations = _goal_record_inputs(
                tx,
                program,
                (plan or {}).get("goal_records", []),
                project_root=project_root,
                links=links,
            )
            previous = next((row for row in reversed(revisions) if row["version"] < program["version"]), None)
            child_ids = [row["id"] for row in children]
            tx.cursor.execute(
                sql.SQL(
                    "SELECT * FROM {}.bridge_attempts WHERE project_id=ANY(%s) AND disposition='active' ORDER BY id"
                ).format(sql.Identifier(tx.schema)),
                (child_ids,),
            )
            attempts = [dict(row) for row in tx.cursor.fetchall()]
            return _planning_call(
                checkpoint,
                program,
                children,
                links,
                as_of=as_of,
                previous_snapshot=None
                if previous is None
                else _planning_call(read_plan, previous["notes"])["forecast_snapshot"],
                attempts=attempts,
                tests=tests,
                specifications=specs,
                dependencies=dependencies,
                goal_record_observations=observations,
            )

    @operation_method
    def program_forecast(
        self, record_id: str, *, as_of: str, native_context_id: str | None = None, project_root: Path | None = None
    ) -> dict[str, Any]:
        with self.kernel.transaction(read_only=True) as tx:
            program, children, dependencies, links, tests, specs = _program_inputs(
                tx,
                record_id,
                native_context_id=native_context_id,
                project_root=project_root,
            )
            plan = _planning_call(read_plan, program.get("notes"))
            if plan is None:
                _error("program_plan_required", "Forecast requires a current program planning revision")
            observations = _goal_record_inputs(
                tx, program, plan.get("goal_records", []), project_root=project_root, links=links
            )
            pace = plan["forecast_snapshot"]["pace"]
            evidence = _estimate_evidence(
                tx,
                None,
                _record_scope(tx, program, project_root=project_root),
                project_root=project_root,
                bases=_planning_call(pace_bases, pace)
                + [
                    basis
                    for child in children
                    for basis in _planning_call(estimate_bases, child.get("planning_estimate"))
                ],
                lock=False,
            )
            return _planning_call(
                forecast_report,
                program,
                children,
                dependencies,
                links,
                as_of=as_of,
                tests=tests,
                specifications=specs,
                goal_record_observations=observations,
                pace_evidence=evidence,
                estimate_evidence=evidence,
            )

    @operation_method
    def program_forecast_change(
        self,
        record_id: str,
        *,
        from_version: int | None = None,
        to_version: int | None = None,
        native_context_id: str | None = None,
        project_root: Path | None = None,
    ) -> dict[str, Any]:
        with self.kernel.transaction(read_only=True) as tx:
            program = _required(tx, "projects", record_id)
            if program["kind"] != "program":
                _error("program_required", "Forecast comparison requires a program record")
            scope = _record_scope(tx, program, project_root=project_root)
            if (
                native_context_id is not None
                and _bound_application_scope(tx, native_context_id, project_root=project_root) != scope
            ):
                _error("application_scope_mismatch", "The initialized subject does not match this program")
            revisions = _planning_history(tx, record_id)
            current = next(
                (row for row in reversed(revisions) if to_version is None or row["version"] == to_version), None
            )
            previous = next(
                (
                    row
                    for row in reversed(revisions)
                    if current is not None
                    and row["version"] < current["version"]
                    and (from_version is None or row["version"] == from_version)
                ),
                None,
            )
            if previous is None or current is None:
                _error("planning_revisions_required", "Select two actual increasing program planning revisions")
            _scope_matches(tx, previous, scope, project_root=project_root)
            _scope_matches(tx, current, scope, project_root=project_root)
            return {**_planning_call(forecast_change, previous, current), "canonical_writes": 0}

    @operation_method
    def set_project_authorization(
        self, record_id: str, request: ProjectAuthorizationChange, *, project_root: Path | None = None
    ) -> dict[str, Any]:
        """Apply owner-directed ordering to the existing project row.

        This operation neither changes membership nor revokes initiated
        attempts.

        The service checks the actual initialized caller binding. Prime
        Builder and Loyal Opposition sessions cannot perform owner ordering
        operations. The administrative writer has no native context; actor
        text records attribution and is not caller authentication
        (GOV-PROJECT-IMPLEMENTATION-AUTHORIZATION-001).
        """
        with self.kernel.transaction() as tx:
            _assert_owner_operation(tx, request)
            current = _required(tx, "projects", record_id, lock=True)
            _assert_request_scope(tx, current, request, project_root=project_root)
            if current["version"] != request.expected_version:
                _error(
                    "cas_conflict",
                    "Read the current project before changing authorization",
                    id=record_id,
                    expected=request.expected_version,
                    actual=current["version"],
                )
            if current["kind"] != "project":
                _error("program_not_authorizable", "Programs have no execution authorization")
            if current["status"] != "active":
                _error("project_closed", "A closed project cannot change execution ordering")
            if record_id == "PROJECT-GTKB-NEW-WORK-INTAKE" and request.authorization != "not authorized":
                _error("intake_not_authorizable", "Move intake work to its execution project before dispatch")
            if current["authorization"] == request.authorization:
                return current
            return _write(
                tx, "projects", record_id, {"authorization": request.authorization}, request, project_root=project_root
            )

    @operation_method
    def retire_project(
        self, record_id: str, request: ProjectRetirement, *, project_root: Path | None = None
    ) -> dict[str, Any]:
        """Retire one active program or execution project by status only, with history (owner ruling D32).

        Exactly one field changes (``status`` -> ``retired``) at version+1 with one history row carrying the
        actor and reason. Kind, authorization, parent, dates, ``completed_at`` (the commit date of a verified
        project only), every membership row (a closed member's active membership is its preserved parent),
        every formal link, every dependency row and every child project are untouched: retiring a project
        never erases a parent or collectively rewrites sibling results (GOV-WORK-ITEM-TERMINAL-STATE-001,
        GOV-PROJECT-VERIFIED-COMPLETION-RETIREMENT-001). Open members are refused, not retired with it, and
        retirement is not implementation verification (GOV-STANDING-BACKLOG-001). An already-closed project
        is refused rather than re-closed. Programs are retirable; a retired program keeps its children and
        cannot acquire new ones (amend_project refuses a non-active program parent).
        """
        with self.kernel.transaction() as tx:
            current = _required(tx, "projects", record_id, lock=True)
            if current["version"] != request.expected_version:
                _error(
                    "cas_conflict",
                    "Read the current project before retiring it",
                    id=record_id,
                    expected=request.expected_version,
                    actual=current["version"],
                )
            if current["status"] != "active":
                _error(
                    "project_closed",
                    "A closed project keeps its terminal state; retirement is not a second closure",
                    id=record_id,
                    status=current["status"],
                )
            if record_id == "PROJECT-GTKB-NEW-WORK-INTAKE":
                _error("project_structure_frozen", "The standing intake project is not retirable", id=record_id)
            members = _open_members(tx, current)
            if members["work_item_ids"] or members["project_ids"]:
                _error(
                    "members_open",
                    "Retire or re-home the open members first; retirement never closes them collectively",
                    id=record_id,
                    **members,
                )
            attempts = _active_attempts(tx, project_id=record_id)
            if attempts:
                _error(
                    "attempt_active",
                    "An active bridge attempt holds reviewed or in-progress work of this project",
                    id=record_id,
                    attempt_ids=attempts,
                )
            dependants = _active_dependants(tx, record_id)
            if dependants:
                _error(
                    "dependants_open",
                    "Active projects still require this prerequisite to reach a state a retired project cannot",
                    id=record_id,
                    dependency_ids=sorted(row["id"] for row in dependants),
                    dependent_project_ids=sorted({row["dependent_project_id"] for row in dependants}),
                )
            return _write(tx, "projects", record_id, {"status": "retired"}, request, project_root=project_root)

    @operation_method
    def amend_membership_scope(
        self, record_id: str, request: MembershipScopeMutation, *, project_root: Path | None = None
    ) -> dict[str, Any]:
        """Classify one existing membership without changing its topology or project authorization."""
        with self.kernel.transaction() as tx:
            _required(tx, "project_work_item_memberships", record_id, lock=True)
            return _write(
                tx,
                "project_work_item_memberships",
                record_id,
                {"application_scope": request.fields.application_scope},
                request,
                project_root=project_root,
            )

    @operation_method
    def amend_project_formal_link(
        self, record_id: str, request: ProjectFormalLinkMutation, *, project_root: Path | None = None
    ) -> dict[str, Any]:
        """Amend formal roots or retire obsolete links without changing authorization."""
        with self.kernel.transaction() as tx:
            current = tx.get("project_artifact_links", {"id": record_id}, lock=True)
            actual = current["version"] if current else 0
            if actual != request.expected_version:
                _error("cas_conflict", "Read the current formal link before changing it", actual=actual, id=record_id)
            fields = request.fields.model_dump(exclude_unset=True)
            if current and current["artifact_type"] != "spec":
                if current.get("application_scope") is None and set(fields) == {"application_scope"}:
                    # Direct classification changes no obsolete relationship
                    # identity or status. Later retirement stays status-only.
                    return _write(tx, "project_artifact_links", record_id, fields, request, project_root=project_root)
                # Imported bridge references and completion guards are obsolete
                # relationships. Retire their status without rewriting identity,
                # creating a substitute, or touching Git activation evidence.
                if (
                    current["artifact_type"] not in {"bridge_thread", "completion_guard"}
                    or current["status"] != "active"
                    or fields != {"status": "retired"}
                ):
                    _error("invalid_formal_link", "Only status-only retirement of an obsolete relationship is allowed")
                _execution_project(tx, current["project_id"])
                return _write(tx, "project_artifact_links", record_id, fields, request, project_root=project_root)
            candidate = {**(current or {"status": "active"}), **fields}
            if not candidate.get("project_id") or not candidate.get("artifact_ref"):
                _error("formal_link_endpoint_required", "A formal link names an execution project and formal record")
            if current and any(candidate[key] != current[key] for key in ("project_id", "artifact_ref")):
                _error("formal_link_identity_frozen", "Retire the old relationship and create the intended new one")
            if candidate["status"] not in {"active", "retired"}:
                _error("invalid_formal_link_transition", "A formal relationship is active or retired")
            if not current and candidate["status"] != "active":
                _error("invalid_formal_link_transition", "A new formal relationship starts active")
            # Bridge effects and finalization hold the same project row. A root
            # change cannot cross an effect, and later operations re-read roots.
            _execution_project(tx, candidate["project_id"])
            if candidate["status"] == "active":
                source = _required(tx, "specifications", candidate["artifact_ref"], lock=True)
                if source["status"] != "active":
                    _error("inactive_formal_source", "An active formal link requires a current active formal record")
                duplicates = _related(
                    tx,
                    "project_artifact_links",
                    project_id=candidate["project_id"],
                    artifact_type="spec",
                    artifact_ref=candidate["artifact_ref"],
                    status="active",
                )
                if any(row["id"] != record_id for row in duplicates):
                    _error("duplicate_formal_link", "This project already has an active relationship to the source")
            return _write(
                tx,
                "project_artifact_links",
                record_id,
                fields,
                request,
                project_root=project_root,
                defaults={"artifact_type": "spec", "relationship": "governed_by", "status": "active"},
            )

    @operation_method
    def amend_dependency(
        self, record_id: str, request: DependencyMutation, *, project_root: Path | None = None
    ) -> dict[str, Any]:
        with self.kernel.transaction() as tx:
            current = tx.get("project_dependencies", {"id": record_id}, lock=True)
            actual = current["version"] if current else 0
            if actual != request.expected_version:
                _error(
                    "cas_conflict",
                    "Read the current dependency before changing it",
                    id=record_id,
                    expected=request.expected_version,
                    actual=actual,
                )
            fields = request.fields.model_dump(exclude_unset=True)
            if current and current.get("application_scope") is None and set(fields) == {"application_scope"}:
                # Classifying recorded history changes neither dependency graph
                # nor closed-project state; ordinary amendments keep their guards.
                return _write(tx, "project_dependencies", record_id, fields, request, project_root=project_root)
            candidate = {
                **(current or {"id": record_id, "status": "active", "dependency_kind": "requires_project_state"}),
                **fields,
            }
            if not current and fields.get("status", "active") != "active":
                _error("invalid_dependency_transition", "A new dependency starts active")
            for key in ("dependent_project_id", "prerequisite_project_id"):
                if not candidate.get(key):
                    _error("dependency_endpoint_required", "A dependency names both execution projects")
                _required(tx, "projects", candidate[key])
            # Publication and finalization hold this same project row before
            # reading its edges. New or changed prerequisites cannot cross an effect.
            affected = {candidate["dependent_project_id"]}
            if current:
                affected.add(current["dependent_project_id"])
            for project_id in sorted(affected):
                project = _required(tx, "projects", project_id, lock=True)
                if project["kind"] != "project":
                    _error("invalid_dependency_endpoint", "Dependencies sequence execution projects")
                if candidate["status"] == "active" and project["status"] != "active":
                    _error("closed_dependent_project", "A closed project cannot acquire or change an active dependency")
            prerequisite = _required(tx, "projects", candidate["prerequisite_project_id"])
            if (
                candidate["status"] == "active"
                and prerequisite["status"] in {"retired", "cancelled", "verified"}
                and prerequisite["status"] != candidate.get("required_prerequisite_state")
            ):
                _error("unreachable_dependency", "The closed prerequisite cannot reach the requested state")
            if candidate.get("related_work_item_id"):
                _required(tx, "work_items", candidate["related_work_item_id"])
            graph = [row for row in _related(tx, "project_dependencies", status="active") if row["id"] != record_id]
            projects = {row["id"]: row for row in _related(tx, "projects")}
            validate_project_dependencies(graph + [candidate], projects)
            return _write(
                tx,
                "project_dependencies",
                record_id,
                fields,
                request,
                project_root=project_root,
                defaults={
                    "status": "active",
                    "dependency_kind": "requires_project_state",
                    "registry_version": 1,
                    "provenance": request.actor,
                    "blocking_status": "open",
                },
            )

    @operation_method
    def project_readiness(self, project_id: str, gate: str = "readiness") -> dict[str, Any]:
        with self.kernel.transaction(read_only=True) as tx:
            return _project_dependency_readiness(tx, project_id, gate)

    def _reconcile_work_item_resolution(
        self,
        tx: PostgresTransaction,
        current: dict[str, Any],
        request: WorkItemMutation,
        *,
        project_root: Path | None = None,
    ) -> dict[str, Any]:
        """Reconcile historical resolution through the existing writer, without reopening committed work.

        The caller supplies the owner's inspected final disposition. Missing canonical commit facts
        never establish that historical physical work was uncommitted; Git-shaped evidence requires
        inspection instead. This branch does not move an ordinary valid single-parent work item.
        """
        if current["version"] != request.expected_version:
            _error("cas_conflict", "Read the current work item before correcting its resolution", id=current["id"])
        scope = _assert_request_scope(tx, current, request, project_root=project_root)
        target = request.fields.resolution_status
        supplied_evidence = "completion_evidence" in request.fields.model_fields_set
        open_historical_completion = (
            current["resolution_status"] == "open" and target == "verified" and supplied_evidence
        )
        clear_rejected_evidence = (
            supplied_evidence
            and target == "open"
            and current["resolution_status"] in {"verified", "implemented", "resolved"}
            and request.fields.completion_evidence == ""
        )
        if supplied_evidence and (
            not clear_rejected_evidence
            and (
                target != "verified"
                or current["resolution_status"] not in {"open", "verified", "implemented", "resolved"}
                or request.fields.completion_evidence is None
            )
        ):
            _error(
                "invalid_completion_evidence",
                "Historical correction accepts exact completion evidence to verified "
                "or an explicit empty clear to open",
            )
        if target == current["resolution_status"] and not supplied_evidence:
            if request.project_id and _current_parent(tx, current["id"])["project_id"] != request.project_id:
                _error("membership_move_required", "Use the atomic membership move operation")
            return {"work_item": current, **_membership_facts(tx, current)}
        if current["resolution_status"] == "open" and not open_historical_completion:
            _error(
                "work_item_frozen",
                "Ordinary work cannot set verified; project commit confirms it. Use the retirement route for retired",
            )
        legacy = {
            "implemented": "verified",
            "resolved": "verified",
            "not yet implemented": "open",
            "not_yet_implemented": "open",
            "wont_fix": "retired",
            "not_a_defect": "retired",
            "deferred": "retired",
            "drop": "retired",
        }
        false_completion = current["resolution_status"] in {"verified", "implemented", "resolved"} and target == "open"
        historical_retirement = current["resolution_status"] == "resolved" and target == "retired"
        if (
            legacy.get(current["resolution_status"]) != target
            and not false_completion
            and not historical_retirement
            and not (current["resolution_status"] == target == "verified" and supplied_evidence)
            and not open_historical_completion
        ):
            _error("work_item_frozen", "The requested postimage is not a lawful legacy resolution correction")
        if target != "open" and request.project_id:
            _error("membership_move_required", "Status-only normalization preserves recorded memberships")
        memberships = _related(tx, "project_work_item_memberships", work_item_id=current["id"])
        active = [member for member in memberships if member["status"] == "active"]
        parents = {member["project_id"] for member in active}
        project_ids = parents | ({request.project_id} if request.project_id else set())
        projects = {project_id: _required(tx, "projects", project_id, lock=True) for project_id in sorted(project_ids)}
        tx.cursor.execute(
            sql.SQL(
                "SELECT id,disposition,terminal_commit FROM {}.bridge_attempts WHERE work_item_id=%s FOR UPDATE"
            ).format(sql.Identifier(tx.schema)),
            (current["id"],),
        )
        attempts = tx.cursor.fetchall()
        live = [attempt["id"] for attempt in attempts if attempt["disposition"] == "active"]
        if live:
            _error(
                "attempt_active",
                "Conclude or reconcile the active attempt before correcting resolution",
                attempt_ids=live,
            )
        terminal = [
            attempt for attempt in attempts if attempt["disposition"] == "committed" or attempt["terminal_commit"]
        ]
        commits = {attempt["terminal_commit"] for attempt in terminal if attempt["terminal_commit"]}
        for parent in sorted(parents):
            links = _related(
                tx,
                "project_artifact_links",
                project_id=parent,
                artifact_type="git_commit",
                relationship="activation",
                status="active",
            )
            terminal.extend(links)
            commits.update(link["artifact_ref"] for link in links)
        # The supported open-to-verified correction records accepted historical completion without
        # inventing activation for its entire project. Initial imported labels have no predecessor.
        # Older verified evidence repairs are ambiguous and keep their existing correction route.
        accepted_commits: set[str] = set()
        history = tx.history("work_items", {"id": current["id"]})
        completion_fields = {
            "resolution_status",
            "completion_evidence",
            "version",
            "changed_at",
            "changed_by",
            "change_reason",
        }
        for previous, transition in zip(history, history[1:], strict=False):
            prior_state = previous["new_state"]
            state = transition["new_state"]
            historical_evidence = state.get("completion_evidence")
            if not isinstance(historical_evidence, str):
                continue
            match = re.fullmatch(r"git:([0-9a-f]{40}(?:[0-9a-f]{24})?)", historical_evidence)
            prior_business = {key: value for key, value in prior_state.items() if key not in completion_fields}
            business = {key: value for key, value in state.items() if key not in completion_fields}
            if (
                transition["prior_version"] == previous["new_version"]
                and transition["new_version"] == previous["new_version"] + 1
                and prior_state["resolution_status"] == "open"
                and state["resolution_status"] == "verified"
                and match is not None
                and canonical_json_bytes(prior_business) == canonical_json_bytes(business)
            ):
                accepted_commits.add(match.group(1))
        commits.update(accepted_commits)
        if (terminal or accepted_commits) and target != "verified":
            _error(
                "work_item_frozen",
                "Canonical committed terminal facts cannot be reopened or re-homed",
                id=current["id"],
            )
        evidence = current.get("completion_evidence") or ""
        if target == "verified":
            evidence = request.fields.completion_evidence if supplied_evidence else evidence
            if not isinstance(evidence, str):
                _error("invalid_completion_evidence", "Historical completion requires exact Git evidence")
            if (terminal or accepted_commits) and (not commits or commits != {evidence.removeprefix("git:")}):
                _error(
                    "terminal_commit_mismatch",
                    "Historical correction cannot conflict with existing canonical terminal commit facts",
                    id=current["id"],
                )
            if len(active) != 1:
                _error(
                    "invalid_membership",
                    "Historical completion requires one established execution project",
                    work_item_id=current["id"],
                )
            evidence = self._validate_historical_commit(project_root, projects[active[0]["project_id"]], evidence)
        elif not clear_rejected_evidence and (
            re.search(r"\bgit:", evidence, re.IGNORECASE)
            or re.fullmatch(r"[0-9a-fA-F]{7,64}", evidence.strip())
            or re.search(r"\bcommit(?:ted)?(?:\s+as)?[:=\s]+[0-9a-fA-F]{7,64}\b", evidence, re.IGNORECASE)
        ):
            _error(
                "git_reconciliation_required",
                "Inspect the historical Git-shaped completion evidence; missing canonical facts do not prove no commit",
                id=current["id"],
            )
        if target == "open":
            sole = projects[active[0]["project_id"]] if len(active) == 1 else None
            if sole is not None and sole["kind"] == "project" and sole["status"] == "active":
                if request.project_id and request.project_id != sole["id"]:
                    _error(
                        "membership_move_required",
                        "Return under the current parent, then use the atomic move operation",
                    )
                destination = sole
            elif request.project_id:
                destination = _execution_project(tx, request.project_id)
            else:
                _error(
                    "project_required",
                    "Choose one active execution project for missing, multiple or closed parents",
                )
            changed_parents = parents ^ {destination["id"]}
            for member in sorted(active, key=lambda item: item["id"]):
                if member["project_id"] != destination["id"]:
                    _write(
                        tx,
                        "project_work_item_memberships",
                        member["id"],
                        {"status": "removed", "application_scope": scope},
                        Mutation(expected_version=member["version"], actor=request.actor, reason=request.reason),
                        project_root=project_root,
                    )
            existing = next((member for member in memberships if member["project_id"] == destination["id"]), None)
            if existing is None or existing["status"] != "active":
                _write(
                    tx,
                    "project_work_item_memberships",
                    existing["id"] if existing else f"PWM-{uuid4().hex}",
                    {
                        "project_id": destination["id"],
                        "work_item_id": current["id"],
                        "application_scope": scope,
                        "status": "active",
                        "membership_order": (
                            existing["membership_order"] if existing else current.get("implementation_order")
                        ),
                        "source": "domain_service",
                    },
                    Mutation(
                        expected_version=existing["version"] if existing else 0,
                        actor=request.actor,
                        reason=request.reason,
                    ),
                    project_root=project_root,
                )
            for project_id in sorted(changed_parents):
                project = projects[project_id]
                if project["kind"] == "project" and project["authorization"] != "not authorized":
                    _write(
                        tx,
                        "projects",
                        project_id,
                        {"authorization": "not authorized"},
                        Mutation(expected_version=project["version"], actor=request.actor, reason=request.reason),
                        project_root=project_root,
                    )
        if historical_retirement:
            dependants = _open_dependants(tx, current["id"])
            if dependants:
                _error(
                    "dependants_open",
                    "Open work still depends on this item; retire or re-point the dependants first",
                    id=current["id"],
                    dependant_work_item_ids=dependants,
                )
        fields: dict[str, Any] = {"resolution_status": target}
        if target == "verified":
            fields["completion_evidence"] = evidence
            if all(current.get(key) == value for key, value in fields.items()) and (
                current.get("changed_by") == request.actor and current.get("change_reason") == request.reason
            ):
                return {"work_item": current, **_membership_facts(tx, current)}
        elif clear_rejected_evidence:
            # The caller records substantive scope inspection in the existing reason. Physical Git checks alone
            # cannot prove full semantic completion. Native history retains the rejected text in its prior state;
            # an omitted evidence field preserves it, and all terminal/attempt/membership guards above still apply.
            fields["completion_evidence"] = ""
        row = _write(tx, "work_items", current["id"], fields, request, project_root=project_root)
        return {"work_item": row, **_membership_facts(tx, row)}

    @operation_method
    def amend_work_item(
        self, record_id: str, request: WorkItemMutation, *, project_root: Path | None = None
    ) -> dict[str, Any]:
        fields = request.fields.model_dump(exclude_unset=True)
        with self.kernel.transaction() as tx:
            current = tx.get("work_items", {"id": record_id}, lock=True)
            if current is not None and request.project_id is not None:
                parents = {
                    member["project_id"]
                    for member in _related(tx, "project_work_item_memberships", work_item_id=record_id, status="active")
                }
                if parents != {request.project_id}:
                    _assert_owner_operation(tx, request)
            if current and current.get("application_scope") is None and set(fields) == {"application_scope"}:
                row = _write(tx, "work_items", record_id, fields, request, project_root=project_root)
                return {"work_item": row, **_membership_facts(tx, row)}
            if "resolution_status" in fields:
                if fields["resolution_status"] is None:
                    _error("invalid_request", "Resolution status cannot be null")
                if current:
                    if set(fields) - {"resolution_status", "completion_evidence"}:
                        _error("invalid_request", "Resolution correction cannot also amend ordinary work fields")
                    return self._reconcile_work_item_resolution(tx, current, request, project_root=project_root)
                if fields["resolution_status"] != "open":
                    _error("work_item_frozen", "New work starts open; only confirmed project commit sets verified")
            if "completion_evidence" in fields:
                _error("invalid_completion_evidence", "Ordinary work cannot author terminal completion evidence")
            if current:
                membership = _current_parent(tx, record_id)
                project = _execution_project(tx, membership["project_id"])
                if request.project_id and request.project_id != project["id"]:
                    _error("membership_move_required", "Use the atomic membership move operation")
                if current["resolution_status"] != "open" or _active_attempts(
                    tx, work_item_id=record_id, reviewed_only=True
                ):
                    _error("work_item_frozen", "Reviewed or closed work cannot be amended through ordinary intake")
            else:
                if not request.project_id:
                    _error("project_required", "New work requires an existing execution project")
                project = _execution_project(tx, request.project_id)
            state = {**(current or {}), **fields}
            if current and set(fields) == {"application_scope"}:
                # Correct one classification without resolving historical neighbors
                # or granting task-context/effect admission. Ordinary guards above stay.
                self._check_dependencies(tx, record_id, state.get("depends_on_work_items") or [])
                row = _write(
                    tx,
                    "work_items",
                    record_id,
                    fields,
                    request,
                    project_root=project_root,
                    work_scope_correction=True,
                )
                return {"work_item": row, **_membership_facts(tx, row)}
            scope = _record_scope(tx, state, project_root=project_root)
            _scope_matches(tx, project, scope, project_root=project_root)
            # Planning amendments preserve visible evidence gaps in existing work.
            # Creation and changed evidence links still require a complete pair;
            # proposal publication independently rechecks the current evidence.
            if current is None or any(
                state.get(key) != current.get(key) for key in ("source_spec_id", "source_test_id")
            ):
                _work_evidence(tx, state, project_root=project_root)
            self._check_dependencies(tx, record_id, state.get("depends_on_work_items") or [])
            _validate_work_dependency_scopes(tx, state, project["id"], project_root=project_root, lock=True)
            row = _write(
                tx,
                "work_items",
                record_id,
                fields,
                request,
                project_root=project_root,
                defaults={"origin": "manual", "component": "gtkb", "resolution_status": "open", "stage": "created"},
            )
            if not current:
                _write(
                    tx,
                    "project_work_item_memberships",
                    f"PWM-{uuid4().hex}",
                    {
                        "project_id": project["id"],
                        "work_item_id": record_id,
                        "application_scope": row["application_scope"],
                        "status": "active",
                        "membership_order": row.get("implementation_order"),
                        "source": "domain_service",
                    },
                    Mutation(expected_version=0, actor=request.actor, reason=request.reason),
                    project_root=project_root,
                )
                if project["authorization"] != "not authorized":
                    _write(
                        tx,
                        "projects",
                        project["id"],
                        {"authorization": "not authorized"},
                        Mutation(
                            expected_version=project["version"],
                            actor=request.actor,
                            reason=request.reason,
                        ),
                        project_root=project_root,
                    )
            return {"work_item": row, **_membership_facts(tx, row)}

    @staticmethod
    def _check_dependencies(tx: PostgresTransaction, record_id: str, dependencies: list[str]) -> None:
        tx.cursor.execute(
            sql.SQL("SELECT id,depends_on_work_items FROM {}.work_items WHERE id<>%s").format(
                sql.Identifier(tx.schema)
            ),
            (record_id,),
        )
        rows = [dict(row) for row in tx.cursor.fetchall()]
        validate_work_item_dependencies(rows + [{"id": record_id, "depends_on_work_items": dependencies}])

    @operation_method
    def move_work_item(
        self, record_id: str, request: MembershipMove, *, project_root: Path | None = None
    ) -> dict[str, Any]:
        with self.kernel.transaction() as tx:
            _assert_owner_operation(tx, request)
            row = _required(tx, "work_items", record_id, lock=True)
            if row["resolution_status"] != "open" or _active_attempts(tx, work_item_id=record_id, reviewed_only=True):
                _error("work_item_frozen", "Reviewed or closed membership requires reconciliation")
            current = _current_parent(tx, record_id)
            if current["project_id"] != request.source_project_id or current["version"] != request.expected_version:
                _error("cas_conflict", "Read the current membership before moving work")
            projects = [
                _execution_project(tx, project_id)
                for project_id in sorted({request.source_project_id, request.destination_project_id})
            ]
            scope = _record_scope(tx, row, project_root=project_root)
            _scope_matches(tx, current, scope, project_root=project_root)
            for project in projects:
                _scope_matches(tx, project, scope, project_root=project_root)
            if request.native_context_id is not None:
                if _bound_application_scope(tx, request.native_context_id, project_root=project_root) != scope:
                    _error(
                        "application_scope_mismatch", "The bound subject cannot move a foreign workload", id=record_id
                    )
            if request.source_project_id == request.destination_project_id:
                _error("invalid_membership", "A move requires two distinct projects")
            _write(
                tx,
                "project_work_item_memberships",
                current["id"],
                {"status": "removed"},
                request,
                project_root=project_root,
            )
            old_destination = _related(
                tx, "project_work_item_memberships", project_id=request.destination_project_id, work_item_id=record_id
            )
            destination = old_destination[0] if old_destination else None
            updated = _write(
                tx,
                "project_work_item_memberships",
                destination["id"] if destination else f"PWM-{uuid4().hex}",
                {
                    "project_id": request.destination_project_id,
                    "work_item_id": record_id,
                    "application_scope": scope,
                    "status": "active",
                    "membership_order": request.membership_order,
                    "source": "domain_service",
                },
                Mutation(
                    expected_version=destination["version"] if destination else 0,
                    actor=request.actor,
                    reason=request.reason,
                ),
                project_root=project_root,
            )
            for project in projects:
                if project["authorization"] != "not authorized":
                    _write(
                        tx,
                        "projects",
                        project["id"],
                        {"authorization": "not authorized"},
                        Mutation(
                            expected_version=project["version"],
                            actor=request.actor,
                            reason=request.reason,
                        ),
                        project_root=project_root,
                    )
            return {"work_item_id": record_id, "membership": updated}

    @operation_method
    def retire_work_item(
        self, record_id: str, request: WorkItemRetirement, *, project_root: Path | None = None
    ) -> dict[str, Any]:
        """Retire one open work item by status only, with history (owner ruling D32).

        Exactly one field changes (``resolution_status`` -> ``retired``) at version+1 with one history row
        carrying the actor and reason. Stage, title, evidence links (an evidence gap stays visible), the
        predecessor list, notes, the active parent membership and every sibling are untouched: active
        membership describes the current parent, including for closed work, and siblings are never retired
        collectively (GOV-WORK-ITEM-TERMINAL-STATE-001). Already-terminal work is refused rather than
        re-closed; open dependants are refused rather than re-pointed; an active bridge attempt is refused
        because its later project finalization would conflict with retirement. Retirement is not
        implementation verification (GOV-STANDING-BACKLOG-001). The response is the work-item read shape.
        """
        with self.kernel.transaction() as tx:
            current = _required(tx, "work_items", record_id, lock=True)
            if current["version"] != request.expected_version:
                _error(
                    "cas_conflict",
                    "Read the current work item before retiring it",
                    id=record_id,
                    expected=request.expected_version,
                    actual=current["version"],
                )
            if current["resolution_status"] != "open":
                _error(
                    "work_item_frozen",
                    "Reviewed or closed work keeps its terminal state; retirement is not a reopen or a second closure",
                    id=record_id,
                    resolution_status=current["resolution_status"],
                )
            membership = _current_parent(tx, record_id)
            # Same lock order as the bridge (work, then project): effects and finalization serialize with this.
            _execution_project(tx, membership["project_id"])
            attempts = _active_attempts(tx, work_item_id=record_id)
            if attempts:
                _error(
                    "attempt_active",
                    "An active bridge attempt holds this work; conclude or abandon it before retirement",
                    id=record_id,
                    attempt_ids=attempts,
                )
            dependants = _open_dependants(tx, record_id)
            if dependants:
                _error(
                    "dependants_open",
                    "Open work still depends on this item; retire or re-point the dependants first",
                    id=record_id,
                    dependant_work_item_ids=dependants,
                )
            row = _write(
                tx, "work_items", record_id, {"resolution_status": "retired"}, request, project_root=project_root
            )
            return {"work_item": row, **_membership_facts(tx, row)}
