"""Closed Dispatcher Next control and numeric accounting contracts.

These types grant no artifact claim, role, activation or physical launch. The
native selector and qualified gateway consume this contract in later cohorts.
"""

from __future__ import annotations

import hashlib

import json

from datetime import UTC, datetime

from typing import Annotated, Any, Literal

from gtkb_protocol import Request, Text

from pydantic import Field, SerializerFunctionWrapHandler, field_validator, model_serializer, model_validator

MAX_AMOUNT = 9_007_199_254_740_991  # exact integer in both PostgreSQL and Home JSON

MAX_WINDOW_MS = 2_678_400_000  # 31 days

Opaque = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]

Positive = Annotated[int, Field(ge=1, le=2_147_483_647)]

Millis = Annotated[int, Field(ge=0, le=MAX_AMOUNT)]

Window = Annotated[int, Field(ge=1_000, le=MAX_WINDOW_MS)]

MeterMode = Literal["per_transport_attempt", "conservative_session_envelope"]

def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)

class InitialAction(Request):
    kind: Literal["N"] = "N"
    work_item_id: Opaque
    creation_history_id: Positive
    prior_published: tuple[Opaque, Positive] | None = None

class BridgeAction(Request):
    kind: Literal["B"] = "B"
    attempt_id: Opaque
    head_version: Positive

class VerifyAction(Request):
    kind: Literal["V"] = "V"
    attempt_id: Opaque
    head_version: Positive
    requested_at: str

    @field_validator("requested_at")
    @classmethod
    def utc_timestamp(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
            raise ValueError("requested_at must be a canonical UTC timestamp")
        if parsed.isoformat() != value or len(value) > 40:
            raise ValueError("requested_at must use canonical ISO format with +00:00")
        return value

class CommitAction(Request):
    kind: Literal["C"] = "C"
    project_id: Opaque
    cohort: list[tuple[Opaque, Positive]] = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_cohort(self) -> CommitAction:
        if self.cohort != sorted(self.cohort) or len({pair[0] for pair in self.cohort}) != len(self.cohort):
            raise ValueError("cohort must contain distinct attempt IDs in canonical order")
        return self

Action = InitialAction | BridgeAction | VerifyAction | CommitAction

class RetryIdentity(Request):
    policy_id: Opaque
    window_start_ms: Millis
    window_length_ms: Window

    @model_validator(mode="after")
    def epoch_window(self) -> RetryIdentity:
        if self.window_start_ms % self.window_length_ms or self.end_ms > MAX_AMOUNT:
            raise ValueError("retry window must be a finite UTC epoch-grid interval")
        return self

    @property
    def end_ms(self) -> int:
        return self.window_start_ms + self.window_length_ms

class LaunchIdentity(Request):
    domain_revision_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    config_id: Opaque
    config_version: Positive

class TargetIdentity(Request):
    harness_id: Opaque
    harness_version: Positive
    installation_id: Opaque
    qualification_ref: Opaque
    route_id: Opaque
    namespace_ref: Opaque

class BasisKey(Request):
    action: Action
    retry: RetryIdentity
    launch: LaunchIdentity
    target: TargetIdentity

    def action_array(self) -> list[Any]:
        action = self.action
        if isinstance(action, InitialAction):
            prior = list(action.prior_published) if action.prior_published else None
            return ["N", action.work_item_id, action.creation_history_id, prior]
        if isinstance(action, BridgeAction):
            return ["B", action.attempt_id, action.head_version]
        if isinstance(action, VerifyAction):
            return ["V", action.attempt_id, action.head_version, action.requested_at]
        return ["C", action.project_id, [list(pair) for pair in action.cohort]]

    def retry_array(self) -> list[Any]:
        return ["R", self.retry.policy_id, self.retry.window_start_ms, self.retry.window_length_ms]

    def encode(self) -> str:
        launch, target = self.launch, self.target
        return canonical(
            [
                "dn1",
                self.action_array(),
                self.retry_array(),
                ["L", launch.domain_revision_sha256, launch.config_id, launch.config_version],
                [
                    "T",
                    target.harness_id,
                    target.harness_version,
                    target.installation_id,
                    target.qualification_ref,
                    target.route_id,
                    target.namespace_ref,
                ],
            ]
        )

    def same_action(self, other: BasisKey) -> bool:
        return self.action_array() == other.action_array()

    def same_retry(self, other: BasisKey) -> bool:
        return self.same_action(other) and self.retry_array() == other.retry_array()

    @classmethod
    def decode(cls, value: str) -> BasisKey:
        raw = json.loads(value)
        if type(raw) is not list or len(raw) != 5 or raw[0] != "dn1":
            raise ValueError("basis_key must encode [dn1,A,R,L,T]")
        action, retry, launch, target = raw[1:]
        if any(type(part) is not list for part in (action, retry, launch, target)):
            raise ValueError("basis_key components must be closed arrays")
        if len(retry) != 4 or retry[0] != "R" or len(launch) != 4 or launch[0] != "L":
            raise ValueError("invalid retry or launch identity")
        if len(target) != 7 or target[0] != "T" or not action:
            raise ValueError("invalid target or action identity")
        kind = action[0]
        if kind == "N" and len(action) == 4:
            prior = action[3]
            if prior is not None and (type(prior) is not list or len(prior) != 2):
                raise ValueError("prior_published is a published attempt/head pair")
            parsed_action: Action = InitialAction(
                work_item_id=action[1],
                creation_history_id=action[2],
                prior_published=tuple(prior) if prior is not None else None,
            )
        elif kind == "B" and len(action) == 3:
            parsed_action = BridgeAction(attempt_id=action[1], head_version=action[2])
        elif kind == "V" and len(action) == 4:
            parsed_action = VerifyAction(attempt_id=action[1], head_version=action[2], requested_at=action[3])
        elif kind == "C" and len(action) == 3 and type(action[2]) is list:
            if any(type(pair) is not list or len(pair) != 2 for pair in action[2]):
                raise ValueError("invalid cohort pair")
            parsed_action = CommitAction(project_id=action[1], cohort=[tuple(pair) for pair in action[2]])
        else:
            raise ValueError("invalid closed action identity")
        result = cls(
            action=parsed_action,
            retry=RetryIdentity(policy_id=retry[1], window_start_ms=retry[2], window_length_ms=retry[3]),
            launch=LaunchIdentity(domain_revision_sha256=launch[1], config_id=launch[2], config_version=launch[3]),
            target=TargetIdentity(
                harness_id=target[1],
                harness_version=target[2],
                installation_id=target[3],
                qualification_ref=target[4],
                route_id=target[5],
                namespace_ref=target[6],
            ),
        )
        if result.encode() != value:
            raise ValueError("basis_key must use the exact canonical encoding")
        return result

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

ProcessCreation = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[0-9]+$")]

PublicPath = Annotated[str, Field(min_length=1, max_length=32768)]

class ExecutingProcess(Request):
    """Existing process_identity projection plus qualified installed-image pin."""

    pid: Positive
    created_at: ProcessCreation
    executable: PublicPath
    executable_sha256: Digest

class WindowsHostIdentity(Request):
    machine_guid: Opaque
    booted_at: ProcessCreation

class PreparedHolderObservation(Request):
    """Constructor-derived creator facts saved before the admitted job runs."""

    host: WindowsHostIdentity
    namespace_ref: Opaque
    job_name: Annotated[str, Field(min_length=1, max_length=260)]
    creator: ExecutingProcess
    release_sha256: Digest

class DispatchObservation(Request):
    basis_key: str
    try_no: Annotated[int, Field(ge=1, le=1000)]
    ended_at: str | None = None
    prepared_holder: PreparedHolderObservation | None = None

    @field_validator("basis_key")
    @classmethod
    def closed_basis(cls, value: str) -> str:
        BasisKey.decode(value)
        return value

    @field_validator("ended_at")
    @classmethod
    def end_stamp(cls, value: str | None) -> str | None:
        return VerifyAction.utc_timestamp(value) if value is not None else None

    @model_validator(mode="after")
    def prepared_identity(self) -> DispatchObservation:
        if self.prepared_holder is not None:
            target = BasisKey.decode(self.basis_key).target
            expected_job = "GTKB-DN-" + hashlib.sha256(f"{self.basis_key}\n{self.try_no}".encode()).hexdigest()
            if (
                self.prepared_holder.namespace_ref != target.namespace_ref
                or self.prepared_holder.job_name != expected_job
            ):
                raise ValueError("Prepared origin must name this exact admitted identity")
        return self

    def carrier(self) -> dict:
        """Preserve the exact three-fact legacy shape when origin is unknown."""
        result = self.model_dump(mode="json")
        if self.prepared_holder is None:
            result.pop("prepared_holder")
        return result

class EnvelopeCapability(Request):
    mode: MeterMode
    qualification_ref: Opaque
    max_transport_attempts: Annotated[int, Field(ge=1, le=10_000)]
    max_input_tokens: Annotated[int, Field(ge=1, le=MAX_AMOUNT)]
    max_output_tokens: Annotated[int, Field(ge=1, le=MAX_AMOUNT)]
    max_micro_usd: Annotated[int, Field(ge=1, le=MAX_AMOUNT)]
    max_session_ms: Annotated[int, Field(ge=1000, le=MAX_WINDOW_MS)]
    internal_retries_bounded: bool
    overlapped_windows_reserved: bool

    @model_validator(mode="after")
    def envelope_proof(self) -> EnvelopeCapability:
        if self.max_input_tokens + self.max_output_tokens > MAX_AMOUNT:
            raise ValueError("combined qualified token bound exceeds the exact numeric representation")
        if self.mode == "conservative_session_envelope" and not (
            self.internal_retries_bounded and self.overlapped_windows_reserved
        ):
            raise ValueError("session envelope must qualify all retries and overlapped windows")
        return self

class OwnedJobLocator(Request):
    kind: Literal["owned_windows_job"] = "owned_windows_job"
    namespace_ref: Opaque
    job_name: Annotated[str, Field(min_length=1, max_length=260)]
    creator: ExecutingProcess
    root: ExecutingProcess | None = None
    host: WindowsHostIdentity | None = None
    actual_context_id: (
        Annotated[str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")] | None
    ) = None

    @model_serializer(mode="wrap")
    def observed_context_carrier(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """An unobserved native session keeps the original physical JSON shape."""
        result = handler(self)
        if self.actual_context_id is None:
            result.pop("actual_context_id", None)
        return result

class HostContextLocator(Request):
    """A qualified host enumerator's exact execution, not an arbitrary OS PID."""

    kind: Literal["qualified_host_context"] = "qualified_host_context"
    namespace_ref: Opaque
    host: ExecutingProcess
    actual_context_id: Opaque

class QualifiedModelIdentity(Request):
    """Protected writer attribution receipt; this DTO alone grants no authority."""

    canonical_model_id: Opaque
    mapping_sha256: Digest
    config_id: Opaque
    config_version: Positive
    test_id: Text
    test_version: Positive

class ActualModelObservation(Request):
    """Observed metadata; native_cursor_runtime_selection discloses backend identity unknown."""

    model_id: Text
    source: Literal[
        "provider_response",
        "sdk_host_metadata",
        "native_cli_response",
        "native_cursor_response_hook",
        "native_cursor_runtime_selection",
    ]
    observation_ref: Opaque
    observed_at: str
    qualified_identity: QualifiedModelIdentity | None = None

    @model_validator(mode="after")
    def actual_metadata(self) -> ActualModelObservation:
        VerifyAction.utc_timestamp(self.observed_at)
        if self.model_id != self.model_id.strip() or self.model_id.casefold() in {
            "unknown",
            "requested",
            "tbd",
            "none",
            "n:a",
            "n/a",
        }:
            raise ValueError("Absent actual model remains null, never a placeholder")
        return self

class ExecutionObservation(Request):
    format: Literal["gtkb.native.execution.v1"] = "gtkb.native.execution.v1"
    execution_id: Opaque
    native_context_id: Text
    enrolled_at: str
    target: TargetIdentity
    provider_id: Opaque
    # Native enrollment chooses a registered surface. Legacy single-result
    # history remains readable; no surface is inferred for ambiguous portfolios.
    surface_key: Text | None = None
    release_sha256: Digest
    physical: Annotated[OwnedJobLocator | HostContextLocator, Field(discriminator="kind")]
    actual_model: ActualModelObservation | None = None
    ended_at: str | None = None
    # Original policy ownership for failed meterless recovery, never original-object or I/O authority.
    supervised_formal_owner: str | None = None

    @model_serializer(mode="wrap")
    def retained_supervised_owner(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result: dict[str, Any] = handler(self)
        if self.supervised_formal_owner is None:
            result.pop("supervised_formal_owner", None)
        return result

    @model_validator(mode="after")
    def current_execution(self) -> ExecutionObservation:
        VerifyAction.utc_timestamp(self.enrolled_at)
        if self.physical.namespace_ref != self.target.namespace_ref:
            raise ValueError("The physical locator must name the exact qualified namespace")
        if self.supervised_formal_owner is not None:
            owner = ExecutionMeterOwner.decode(self.supervised_formal_owner)
            if (
                (owner.native_context_id, owner.execution_id, owner.target)
                != (self.native_context_id, self.execution_id, self.target)
                or not isinstance(self.physical, OwnedJobLocator)
                or self.physical.job_name
                != "GTKB-DN-EXEC-" + hashlib.sha256(self.supervised_formal_owner.encode()).hexdigest()
            ):
                raise ValueError("Retained supervised ownership must match this exact enrolled Job")
        if self.actual_model and datetime.fromisoformat(self.actual_model.observed_at) < datetime.fromisoformat(
            self.enrolled_at
        ):
            raise ValueError("An actual model observation cannot precede enrollment")
        if self.ended_at is not None:
            VerifyAction.utc_timestamp(self.ended_at)
            if datetime.fromisoformat(self.ended_at) < datetime.fromisoformat(self.enrolled_at) or (
                self.actual_model is not None
                and datetime.fromisoformat(self.ended_at) < datetime.fromisoformat(self.actual_model.observed_at)
            ):
                raise ValueError("Physical end cannot precede the execution observation")
        return self

class BindingCarrier(Request):
    native_context_id: Text
    session_context_id: Text | None
    subject: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")] | None
    role: Literal["prime-builder", "loyal-opposition"] | None
    created_at: str
    minimum_idempotency_identity: Text | None
    execution_observation: ExecutionObservation | None

    @model_validator(mode="after")
    def exact_state(self) -> BindingCarrier:
        VerifyAction.utc_timestamp(self.created_at)
        if self.subject is not None and self.subject.casefold() == "gtkb" and self.subject != "gtkb":
            raise ValueError("The reserved GTKB subject has one canonical spelling")
        binding = (self.session_context_id, self.subject, self.role, self.minimum_idempotency_identity)
        if any(item is None for item in binding) and any(item is not None for item in binding):
            raise ValueError("An execution enrollment cannot be a partial initialized binding")
        if all(item is None for item in binding) and self.execution_observation is None:
            raise ValueError("An uninitialized row requires an attributable execution enrollment")
        if (
            self.execution_observation is not None
            and self.execution_observation.native_context_id != self.native_context_id
        ):
            raise ValueError("Execution enrollment must name the actual carrier's native context")
        return self

    @property
    def initialization_state(self) -> Literal["execution_enrolled", "initialized"]:
        return "execution_enrolled" if self.session_context_id is None else "initialized"

class ExecutedTestSelector(Request):
    test_file: PublicPath
    test_class: Text | None
    test_function: Text | None

class ExecutionMeterOwner(Request):
    """Ordinary execution's disjoint correlation in the existing meter relation.

    It identifies a measured execution, never a native action or a formal
    session role. The private recorder derives it under current arbitration.
    Native context and execution identities are immutable for this interval.
    """

    kind: Literal["execution_binding"] = "execution_binding"
    native_context_id: Text
    execution_id: Opaque
    config_id: Opaque
    config_version: Positive
    target: TargetIdentity

    def encode(self) -> str:
        target = self.target
        return canonical(
            [
                "DN-EXEC-1",
                self.native_context_id,
                self.execution_id,
                self.config_id,
                self.config_version,
                [
                    "T",
                    target.harness_id,
                    target.harness_version,
                    target.installation_id,
                    target.qualification_ref,
                    target.route_id,
                    target.namespace_ref,
                ],
            ]
        )

    @classmethod
    def decode(cls, value: str) -> ExecutionMeterOwner:
        raw = json.loads(value)
        if type(raw) is not list or len(raw) != 6 or raw[0] != "DN-EXEC-1":
            raise ValueError("Execution accounting requires the disjoint closed enrollment identity")
        target = raw[5]
        if type(target) is not list or len(target) != 7 or target[0] != "T":
            raise ValueError("Execution accounting requires the exact target identity")
        result = cls(
            native_context_id=raw[1],
            execution_id=raw[2],
            config_id=raw[3],
            config_version=raw[4],
            target=TargetIdentity(
                harness_id=target[1],
                harness_version=target[2],
                installation_id=target[3],
                qualification_ref=target[4],
                route_id=target[5],
                namespace_ref=target[6],
            ),
        )
        if result.encode() != value:
            raise ValueError("Execution accounting requires exact canonical encoding")
        return result

class QualificationExecutionEvidence(Request):
    """One native test execution result's evidence; never a second result store.

    The harness capabilities_ref is predeclared native-test:<test_id>. Its
    version stays unchanged during execution; the result writer stamps this
    test row's post-version atomically with the existing pass/fail result.
    """

    format: Literal["gtkb.native.qualification.v1"] = "gtkb.native.qualification.v1"
    qualification_id: Opaque
    test_id: Text
    test_version: Positive
    selector: ExecutedTestSelector
    surface_key: Text | None = None
    target: TargetIdentity
    provider_id: Opaque
    observed_model_id: Text
    model_source: Literal[
        "provider_response",
        "sdk_host_metadata",
        "native_cli_response",
        "native_cursor_response_hook",
        "native_cursor_runtime_selection",
    ]
    release_sha256: Digest
    # The mapped runtime worker and its registered launcher may be distinct
    # Windows processes. Legacy history remains decodable; protected launches
    # require the separately observed launcher pin before effects.
    executable_sha256: Digest
    launcher_executable_sha256: Digest | None = None
    # Route-only results remain readable. Only a native whole-contract
    # execution may stamp the captured policy after measuring all twelve exits.
    dispatcher_policy_sha256: Digest | None = None
    canonical_model_id: Opaque | None = None
    model_mapping_sha256: Digest | None = None
    registered_invocation_sha256: Digest
    scope: Literal["dispatcher", "supervised_formal"] = "dispatcher"
    metering: EnvelopeCapability | None = None
    capabilities: list[Opaque] = Field(min_length=1, max_length=64)
    source_result_sha256: Digest
    installed_result_sha256: Digest
    actual_roster_observation_sha256: Digest
    terminal_cleanup_observation_sha256: Digest
    completed_at: str

    @model_validator(mode="after")
    def exact_qualification(self) -> QualificationExecutionEvidence:
        VerifyAction.utc_timestamp(self.completed_at)
        if self.model_source == "native_cursor_runtime_selection" and (
            self.scope != "supervised_formal" or self.target.installation_id != "cursor"
        ):
            raise ValueError(
                "Runtime selection records completed Cursor supervised evidence, not metered Dispatcher proof"
            )
        if self.observed_model_id != self.observed_model_id.strip() or self.observed_model_id.casefold() in {
            "unknown",
            "requested",
            "tbd",
            "none",
            "n:a",
            "n/a",
        }:
            raise ValueError("Qualification records an actual metadata value, never a placeholder")
        if self.target.qualification_ref != self.qualification_id:
            raise ValueError("Route must name the same exact measured qualification")
        if self.scope == "dispatcher":
            if self.metering is None or self.metering.qualification_ref != self.qualification_id:
                raise ValueError("Dispatcher metering must name the same exact measured qualification")
        else:
            required = {"actual_model", "containment", "native_record_writer", "private_transport"}
            # Historical native TEST evidence remains decodable. This retired
            # observation is optional history, never a current acceptance duty.
            optional = {"pre_provider_enrollment", "cli_stream_metadata", "cli_tool_identity_barrier", "guard_refusal"}
            if (
                self.metering is not None
                or self.dispatcher_policy_sha256 is not None
                or self.launcher_executable_sha256 is None
                or self.surface_key is None
                or not required <= set(self.capabilities) <= required | optional
            ):
                raise ValueError(
                    "Supervised formal evidence measures protected model/effect boundaries, not Dispatcher admission"
                )
        if (self.canonical_model_id is None) != (self.model_mapping_sha256 is None):
            raise ValueError("Canonical qualification identity and exact mapping pin are one optional receipt")
        if self.capabilities != sorted(set(self.capabilities)):
            raise ValueError("Measured capability names must be distinct and canonical")
        return self
