CREATE TABLE {schema}.specifications (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    title TEXT NOT NULL,
    description TEXT,
    priority TEXT,
    scope TEXT,
    section TEXT,
    handle TEXT,
    tags JSONB,
    status TEXT NOT NULL,
    assertions JSONB,
    type TEXT,
    authority TEXT,
    provisional_until TEXT,
    constraints JSONB,
    affected_by JSONB,
    testability TEXT,
    source_paths JSONB,
    implementation_verified_at TIMESTAMPTZ,
    retired_at TIMESTAMPTZ,
    parent TEXT,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    FOREIGN KEY (parent) REFERENCES {schema}.specifications(id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (provisional_until) REFERENCES {schema}.specifications(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE {schema}.specification_deliberation_sources (
    spec_id TEXT NOT NULL,
    deliberation_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    source_role TEXT,
    added_at TIMESTAMPTZ NOT NULL,
    added_by TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    PRIMARY KEY (spec_id, deliberation_id),
    FOREIGN KEY (spec_id) REFERENCES {schema}.specifications(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE {schema}.test_procedures (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    title TEXT NOT NULL,
    type TEXT,
    content TEXT,
    assertion_count INTEGER,
    last_execution_status TEXT,
    last_executed_at TIMESTAMPTZ,
    last_executed_on DATE,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    CHECK (last_executed_at IS NULL OR last_executed_on IS NULL)
);

CREATE TABLE {schema}.operational_procedures (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    title TEXT NOT NULL,
    type TEXT,
    variables JSONB,
    steps JSONB,
    known_failure_modes JSONB,
    last_verified_at TIMESTAMPTZ,
    last_verified_on DATE,
    last_corrected_at TIMESTAMPTZ,
    last_corrected_on DATE,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    CHECK (last_verified_at IS NULL OR last_verified_on IS NULL),
    CHECK (last_corrected_at IS NULL OR last_corrected_on IS NULL)
);

CREATE TABLE {schema}.environment_config (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    environment TEXT NOT NULL,
    category TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    sensitive BOOLEAN NOT NULL DEFAULT FALSE,
    notes TEXT,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    UNIQUE (environment, category, key)
);

CREATE TABLE {schema}.documents (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    content TEXT,
    tags JSONB,
    status TEXT NOT NULL,
    source_path TEXT,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$')
);

CREATE TABLE {schema}.tests (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    title TEXT NOT NULL,
    spec_id TEXT NOT NULL,
    test_type TEXT NOT NULL,
    test_file TEXT,
    test_class TEXT,
    test_function TEXT,
    description TEXT,
    expected_outcome TEXT NOT NULL,
    last_result TEXT,
    last_executed_at TIMESTAMPTZ,
    last_executed_on DATE,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    execution_evidence JSONB CHECK (execution_evidence IS NULL OR jsonb_typeof(execution_evidence) = 'object'),
    FOREIGN KEY (spec_id) REFERENCES {schema}.specifications(id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (last_executed_at IS NULL OR last_executed_on IS NULL)
);

CREATE TABLE {schema}.test_plans (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$')
);

CREATE TABLE {schema}.test_plan_phases (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    plan_id TEXT NOT NULL,
    phase_order INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    gate_criteria TEXT NOT NULL,
    test_ids JSONB,
    last_result TEXT,
    last_executed_at TIMESTAMPTZ,
    last_executed_on DATE,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    FOREIGN KEY (plan_id) REFERENCES {schema}.test_plans(id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (last_executed_at IS NULL OR last_executed_on IS NULL)
);

CREATE TABLE {schema}.work_items (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    title TEXT NOT NULL,
    description TEXT,
    origin TEXT NOT NULL,
    component TEXT NOT NULL,
    source_spec_id TEXT,
    source_test_id TEXT,
    failure_description TEXT,
    resolution_status TEXT NOT NULL,
    priority TEXT,
    stage TEXT NOT NULL DEFAULT 'created',
    implementation_order INTEGER,
    status_detail TEXT,
    source_owner_directive TEXT,
    source_deliberation_query TEXT,
    related_deliberation_ids JSONB,
    related_spec_ids_at_creation JSONB,
    depends_on_work_items JSONB,
    blocks_work_items JSONB,
    acceptance_summary TEXT,
    regression_visibility TEXT,
    completion_evidence TEXT,
    supersedes JSONB,
    superseded_by JSONB,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    FOREIGN KEY (source_spec_id) REFERENCES {schema}.specifications(id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (source_test_id) REFERENCES {schema}.tests(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE {schema}.projects (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('program', 'project')),
    status TEXT NOT NULL DEFAULT 'active',
    "authorization" TEXT,
    repository_ref TEXT CHECK (repository_ref IS NULL OR repository_ref = 'platform' OR repository_ref ~ '^application:[A-Za-z][A-Za-z0-9_-]*$'),
    rank INTEGER,
    parent_project_id TEXT,
    purpose TEXT,
    target_outcome TEXT,
    scope_note TEXT,
    start_date DATE,
    target_date DATE,
    completed_at TIMESTAMPTZ,
    notes TEXT,
    source_project_name TEXT,
    source_subproject_name TEXT,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    planning_estimate JSONB,
    FOREIGN KEY (parent_project_id) REFERENCES {schema}.projects(id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((kind = 'program' AND "authorization" IS NULL AND parent_project_id IS NULL AND repository_ref IS NULL) OR
           (kind = 'project' AND "authorization" IS NOT NULL AND "authorization" IN ('authorized', 'not authorized'))),
    CHECK (id <> 'PROJECT-GTKB-NEW-WORK-INTAKE' OR (kind = 'project' AND "authorization" = 'not authorized'))
);

CREATE TABLE {schema}.project_work_item_memberships (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    project_id TEXT NOT NULL,
    work_item_id TEXT NOT NULL,
    membership_order INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    UNIQUE (project_id, work_item_id),
    FOREIGN KEY (project_id) REFERENCES {schema}.projects(id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (work_item_id) REFERENCES {schema}.work_items(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE {schema}.project_dependencies (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    dependent_project_id TEXT NOT NULL,
    prerequisite_project_id TEXT NOT NULL,
    dependency_kind TEXT NOT NULL DEFAULT 'requires_project_state',
    required_prerequisite_state TEXT NOT NULL DEFAULT 'retired',
    affected_gate TEXT NOT NULL CHECK (affected_gate <> 'authorization'),
    provenance TEXT NOT NULL,
    registry_version INTEGER NOT NULL DEFAULT 1,
    rationale TEXT,
    blocking_status TEXT NOT NULL DEFAULT 'open',
    related_work_item_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    FOREIGN KEY (dependent_project_id) REFERENCES {schema}.projects(id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (prerequisite_project_id) REFERENCES {schema}.projects(id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (related_work_item_id) REFERENCES {schema}.work_items(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE {schema}.project_artifact_links (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    project_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    artifact_ref TEXT NOT NULL,
    relationship TEXT NOT NULL DEFAULT 'related',
    status TEXT NOT NULL DEFAULT 'active',
    notes TEXT,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    FOREIGN KEY (project_id) REFERENCES {schema}.projects(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE {schema}.testable_elements (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    subsystem TEXT NOT NULL,
    page_or_module TEXT NOT NULL,
    name TEXT NOT NULL,
    element_type TEXT NOT NULL,
    expected_behavior TEXT NOT NULL,
    spec_id TEXT,
    applicable_dimensions JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    FOREIGN KEY (spec_id) REFERENCES {schema}.specifications(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE {schema}.deliberations (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    spec_id TEXT,
    work_item_id TEXT,
    source_type TEXT NOT NULL,
    source_ref TEXT,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT,
    participants JSONB,
    outcome TEXT,
    session_id TEXT,
    sensitivity TEXT DEFAULT 'normal',
    redaction_state TEXT DEFAULT 'clean',
    redaction_notes TEXT,
    origin_project TEXT,
    origin_repo TEXT,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    FOREIGN KEY (spec_id) REFERENCES {schema}.specifications(id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (work_item_id) REFERENCES {schema}.work_items(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE {schema}.deliberation_specs (
    deliberation_id TEXT NOT NULL,
    spec_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    role TEXT DEFAULT 'related',
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    PRIMARY KEY (deliberation_id, spec_id),
    FOREIGN KEY (deliberation_id) REFERENCES {schema}.deliberations(id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (spec_id) REFERENCES {schema}.specifications(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE {schema}.deliberation_work_items (
    deliberation_id TEXT NOT NULL,
    work_item_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    role TEXT DEFAULT 'related',
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$'),
    PRIMARY KEY (deliberation_id, work_item_id),
    FOREIGN KEY (deliberation_id) REFERENCES {schema}.deliberations(id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (work_item_id) REFERENCES {schema}.work_items(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE {schema}.canonical_terms (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    canonical_term TEXT NOT NULL,
    definition TEXT NOT NULL,
    authority_level TEXT NOT NULL CHECK (authority_level IN ('platform_core', 'adopter_extension', 'project_local')),
    scope TEXT NOT NULL,
    accepted_synonyms JSONB,
    discouraged_synonyms JSONB,
    linked_artifacts JSONB,
    linked_services JSONB,
    usage_examples JSONB,
    forbidden_uses JSONB,
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN ('candidate', 'active', 'deprecated', 'retired')),
    source_authority TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$')
);

CREATE TABLE {schema}.harnesses (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    harness_name TEXT NOT NULL,
    harness_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'registered',
    invocation_surfaces JSONB,
    capabilities_ref TEXT,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    change_reason TEXT NOT NULL,
    application_scope TEXT CHECK (application_scope ~ '^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$')
);

CREATE TABLE {schema}.record_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    record_type TEXT NOT NULL,
    record_id JSONB NOT NULL,
    prior_version INTEGER CHECK (prior_version IS NULL OR prior_version >= 1),
    new_version INTEGER NOT NULL CHECK (new_version >= 1),
    prior_state JSONB,
    new_state JSONB NOT NULL,
    actor TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    reason TEXT NOT NULL
);

-- Execution enrollment grants no initialized authority. Literal init alone fills
-- immutable attribution; no activity, work container or role history lives here.
CREATE TABLE {schema}.session_init_bindings (
    native_context_id TEXT PRIMARY KEY,
    session_context_id TEXT UNIQUE,
    subject TEXT CHECK (subject ~ '^[A-Za-z][A-Za-z0-9_-]*$' AND (lower(subject) <> 'gtkb' OR subject = 'gtkb')),
    role TEXT CHECK (role IN ('prime-builder', 'loyal-opposition')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    minimum_idempotency_identity TEXT,
    execution_observation JSONB
        CHECK (execution_observation IS NULL OR jsonb_typeof(execution_observation) = 'object'),
    CONSTRAINT session_init_bindings_initialization_shape CHECK (
        (session_context_id IS NOT NULL AND subject IS NOT NULL AND role IS NOT NULL
            AND minimum_idempotency_identity IS NOT NULL)
        OR
        (session_context_id IS NULL AND subject IS NULL AND role IS NULL
            AND minimum_idempotency_identity IS NULL AND execution_observation IS NOT NULL)
    )
);

-- Current coordination facts survive an agent context, but not bridge payloads.
-- Terminal cleanup reduces an attempt to its anti-replay identity/disposition.
CREATE TABLE {schema}.bridge_attempts (
    id TEXT PRIMARY KEY,
    work_item_id TEXT REFERENCES {schema}.work_items(id),
    project_id TEXT REFERENCES {schema}.projects(id),
    head_version INTEGER NOT NULL DEFAULT 0 CHECK (head_version >= 0),
    -- The twelve canonical bridge statuses (SPEC-BRIDGE-STATUS-PHASE-DISTINCT-001; bridge/vocabulary.py).
    head_status TEXT
        CHECK (head_status IN ('ADVISORY', 'BLOCKED', 'GO', 'NEW', 'NO-GO', 'NOT-READY', 'READY', 'REVISED',
            'SUPERSEDED', 'VERDICT-REJECTED', 'VERIFIED', 'WITHDRAWN')),
    disposition TEXT NOT NULL DEFAULT 'active'
        CHECK (disposition IN ('active', 'abandoned', 'withdrawn', 'superseded', 'committed')),
    work_item_version INTEGER,
    proposal_paths JSONB NOT NULL DEFAULT '[]',
    test_targets JSONB NOT NULL DEFAULT '[]',
    spec_versions JSONB NOT NULL DEFAULT '{{}}',
    formal_roots JSONB,
    proposal_context_id TEXT,
    go_context_id TEXT,
    report_context_id TEXT,
    verified_artifacts JSONB,
    finalization_failure TEXT,
    terminal_commit TEXT,
    terminal_author_session_context_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    closed_at TIMESTAMPTZ,
    CHECK (work_item_id IS NOT NULL OR head_status IS NULL OR head_status = 'ADVISORY'),
    CHECK ((work_item_id IS NULL) = (project_id IS NULL))
);

-- DN-A BEGIN: nonpayload Dispatcher coordination. Current-domain export omits it.
ALTER TABLE {schema}.bridge_attempts ADD COLUMN dispatch_observation JSONB;
ALTER TABLE {schema}.bridge_attempts ADD CONSTRAINT bridge_dispatch_observation_check CHECK (
    dispatch_observation IS NULL OR (
        jsonb_typeof(dispatch_observation) = 'object'
        AND dispatch_observation ?& ARRAY['basis_key', 'try_no', 'ended_at']
        AND dispatch_observation - ARRAY['basis_key', 'try_no', 'ended_at', 'prepared_holder'] = '{{}}'::jsonb
        AND (NOT (dispatch_observation ? 'prepared_holder')
             OR dispatch_observation->'prepared_holder' = 'null'::jsonb
             OR jsonb_typeof(dispatch_observation->'prepared_holder') = 'object')
        AND jsonb_typeof(dispatch_observation->'basis_key') = 'string'
        AND octet_length(dispatch_observation->>'basis_key') > 0
        AND jsonb_typeof(dispatch_observation->'try_no') = 'number'
        AND ((dispatch_observation->>'try_no') ~ '^[1-9][0-9]{{0,2}}$'
             OR dispatch_observation->>'try_no' = '1000')
        AND (dispatch_observation->'ended_at' = 'null'::jsonb OR (
            jsonb_typeof(dispatch_observation->'ended_at') = 'string'
            AND (dispatch_observation->>'ended_at') ~
                '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}T[0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}(\.[0-9]{{1,6}})?\+00:00$'
        ))
    )
);

-- No bridge_items foreign key: payload purge must not erase an unconsumed hint.
CREATE TABLE {schema}.bridge_outbox (
    attempt_id TEXT NOT NULL REFERENCES {schema}.bridge_attempts(id),
    version INTEGER NOT NULL CHECK (version >= 1),
    status TEXT NOT NULL
        CHECK (status IN ('ADVISORY', 'BLOCKED', 'GO', 'NEW', 'NO-GO', 'NOT-READY', 'READY', 'REVISED',
            'SUPERSEDED', 'VERDICT-REJECTED', 'VERIFIED', 'WITHDRAWN')),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (attempt_id, version)
);

-- One bounded relation, integer units, no provider payload or lifecycle ledger.
-- window_end_ms=0 denotes physical concurrency, released only at qualified end.
CREATE TABLE {schema}.dispatcher_meter (
    attempt_id TEXT REFERENCES {schema}.bridge_attempts(id),
    basis_key TEXT NOT NULL CHECK (octet_length(basis_key) > 0),
    basis_sha256 TEXT NOT NULL CHECK (basis_sha256 ~ '^[0-9a-f]{{64}}$'),
    try_no INTEGER NOT NULL CHECK (try_no BETWEEN 1 AND 1000),
    call_no INTEGER NOT NULL CHECK (call_no BETWEEN 0 AND 10000),
    dimension_key TEXT NOT NULL CHECK (octet_length(dimension_key) BETWEEN 1 AND 512),
    unit TEXT NOT NULL CHECK (unit IN ('workers', 'documents', 'requests', 'tokens', 'micro_usd')),
    window_start_ms BIGINT NOT NULL CHECK (window_start_ms BETWEEN 0 AND 9007199254740991),
    window_end_ms BIGINT NOT NULL CHECK (window_end_ms BETWEEN 0 AND 9007199254740991),
    reserved_amount BIGINT NOT NULL CHECK (reserved_amount BETWEEN 0 AND 9007199254740991),
    held_amount BIGINT NOT NULL CHECK (held_amount BETWEEN 0 AND reserved_amount),
    consumed_amount BIGINT NOT NULL CHECK (consumed_amount BETWEEN 0 AND 9007199254740991),
    settlement_state TEXT NOT NULL CHECK (settlement_state IN ('held', 'settled', 'unknown')),
    execution_native_context_id TEXT REFERENCES {schema}.session_init_bindings(native_context_id),
    -- METER USAGE BASIS BEGIN
    usage_basis TEXT CONSTRAINT dispatcher_meter_usage_basis_check CHECK (
        usage_basis IS NULL OR (unit IN ('tokens', 'micro_usd') AND call_no > 0 AND (
            (usage_basis = 'missing' AND settlement_state = 'unknown' AND consumed_amount = 0)
            OR (usage_basis = 'reported' AND settlement_state = 'settled')
            OR (usage_basis = 'owner_exception' AND settlement_state = 'settled'
                AND consumed_amount = reserved_amount)
        ))
    ),
    -- METER USAGE BASIS END
    PRIMARY KEY (basis_sha256, try_no, call_no, dimension_key, window_start_ms, window_end_ms),
    CHECK (held_amount + consumed_amount <= 9007199254740991),
    CHECK ((unit IN ('workers', 'documents') AND window_start_ms=0 AND window_end_ms=0)
        OR (unit IN ('requests', 'tokens', 'micro_usd') AND window_end_ms > window_start_ms)),
    CHECK (settlement_state <> 'settled' OR held_amount=0),
    CONSTRAINT dispatcher_meter_exact_owner CHECK (
        (attempt_id IS NOT NULL AND execution_native_context_id IS NULL)
        OR (attempt_id IS NULL AND execution_native_context_id IS NOT NULL)
    )
);
CREATE INDEX dispatcher_meter_dimension_idx ON {schema}.dispatcher_meter(dimension_key, window_start_ms, window_end_ms);
-- DN-A END

CREATE TABLE {schema}.bridge_items (
    attempt_id TEXT NOT NULL REFERENCES {schema}.bridge_attempts(id),
    version INTEGER NOT NULL CHECK (version >= 1),
    status TEXT NOT NULL
        CHECK (status IN ('ADVISORY', 'BLOCKED', 'GO', 'NEW', 'NO-GO', 'NOT-READY', 'READY', 'REVISED',
            'SUPERSEDED', 'VERDICT-REJECTED', 'VERIFIED', 'WITHDRAWN')),
    author_session_context_id TEXT NOT NULL,
    delivery_fence BIGINT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (attempt_id, version)
);

-- One exact successor slot. A successful delivery deletes its claim immediately.
-- Fences never reset when an expired claim is replaced. There is no renewal.
CREATE TABLE {schema}.work_intent_claims (
    attempt_id TEXT PRIMARY KEY REFERENCES {schema}.bridge_attempts(id),
    next_version INTEGER NOT NULL CHECK (next_version >= 1),
    intended_status TEXT NOT NULL
        CHECK (intended_status IN ('ADVISORY', 'BLOCKED', 'GO', 'NEW', 'NO-GO', 'NOT-READY', 'READY', 'REVISED',
            'SUPERSEDED', 'VERDICT-REJECTED', 'VERIFIED', 'WITHDRAWN')),
    predecessor_sha256 TEXT,
    claimant_session_context_id TEXT NOT NULL,
    fence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    request_id TEXT NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp() + interval '600 seconds'
);

CREATE UNIQUE INDEX bridge_one_active_attempt_idx ON {schema}.bridge_attempts(work_item_id)
    WHERE disposition = 'active';
CREATE INDEX bridge_claim_expiry_idx ON {schema}.work_intent_claims(expires_at);

ALTER TABLE {schema}.specification_deliberation_sources
    ADD CONSTRAINT specification_deliberation_sources_deliberation_fk
    FOREIGN KEY (deliberation_id) REFERENCES {schema}.deliberations(id) DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX specifications_status_idx ON {schema}.specifications(status);
CREATE INDEX tests_spec_id_idx ON {schema}.tests(spec_id);
CREATE INDEX work_items_resolution_idx ON {schema}.work_items(resolution_status);
CREATE INDEX projects_status_idx ON {schema}.projects(status, "authorization");
CREATE INDEX project_memberships_work_item_idx ON {schema}.project_work_item_memberships(work_item_id);
-- Closed work keeps its recorded membership history exactly (owner decision
-- 2026-09-10), including zero or several active rows, so no storage-level
-- uniqueness over every work item's active membership exists. Exactly one
-- current execution project per OPEN work item is enforced by the migration
-- validator on import and readback and by the native service's membership
-- writes (intake creates one membership; a move retires the current row and
-- activates the destination inside one serializable transaction). The schema
-- stays declarative: no functions, triggers or views.
CREATE INDEX project_memberships_active_idx ON {schema}.project_work_item_memberships(work_item_id)
    WHERE status = 'active';
CREATE INDEX project_dependencies_prerequisite_idx ON {schema}.project_dependencies(prerequisite_project_id, status);
CREATE INDEX record_history_record_idx ON {schema}.record_history(record_type, new_version);
