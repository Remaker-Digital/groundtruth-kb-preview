# GT-KB target architecture (preview edition)

> **Pre-release preview.** These are sections 1 to 11 of GT-KB's System Architecture Document, which
> describes the target end state. It states design intent. It does not describe released or installed
> behavior. Identifiers such as `ADR-...`, `DCL-...`, `GOV-...` and `REQ-...` name canonical Membase
> records that are not part of this preview. Agent-only instruction sections, agent skill bodies
> and reader notes are left out.
>
> Licensed under the Creative Commons Attribution-NonCommercial 4.0 International license. See
> LICENSE-DOCUMENTATION and NOTICE.

## 1.1 Platform purpose

GT-KB supplies current knowledge and a shared work process to ephemeral agent contexts. It gives an assigned context the instructions, terminology, formal requirements, dependencies, test instructions and work state needed to produce a usable artifact or observable action. Continuity resides in canonical knowledge and work product. The owner supplies requirements, clarifies intent, chooses material tradeoffs and orders work.

The platform addresses context drift, incomplete requirements, unreviewed work and disagreement between intended and actual behavior. Its useful result is work that another context can understand, implement and independently verify against the applicable current specifications and acceptance. Attribution identifies the actual context responsible for a governed act; it does not establish hostile multi-tenant isolation or cryptographic identity assurance. `ADR-ARTIFACT-ORIENTED-DEVELOPMENT-001` defines the work constitution.

Platform development and registered applications share GT-KB's canonical knowledge, CLI and workflow (`ADR-APPLICATION-ISOLATION-CONTRACT-001`), and an application adopts the governance capabilities available to it (`GOV-GTKB-ADOPTION-ENFORCEMENT-001`). Each application's work product resides in its registered independent repository; the current execution project selects that repository. Shared knowledge infrastructure does not merge application products or release cadences. A demonstrated platform limitation is recorded and resolved within the affected scope before dependent application work resumes.

## 1.2 Intended outcomes and evidence

Evaluate GT-KB through attributable work outcomes: usable requirements and tests, coherent artifacts, complete independent review, appropriate accepted endings and reduced routine owner intervention. A requirement or architecture decision states intent. Current source, executed checks, installed observations, actual native behavior and release evidence establish their respective results. A source path, test definition, generated file or earlier passing subset establishes no broader acceptance.

Reports retain the observed scope, exact input and artifact identities, environment, result and limitations. Failed, partial, synthetic, unexecuted and successful outcomes remain distinct. An unavailable required observation leaves the dependent result unassessed. Data corrections use versioned writing and readback, read-only measurement uses attributable observations, and an operational action uses its specified ending. A work-product project uses the complete independently reviewed Git result described by `GOV-PROJECT-VERIFIED-COMPLETION-RETIREMENT-001`; a plan or child commit does not prove the overall goal.

## 1.3 Architectural invariants

Author each agent instruction once in the SAD. Membase holds canonical testable specifications and architecture decisions; the SAD cites their IDs. Canonical domain facts, authored prose and actual Git facts retain their distinct responsibilities. `ADR-SAD-SINGLE-AUTHORED-SOURCE-001` defines this authoring and projection architecture.

Prefer simplification and removal to added mechanisms. When two designs meet the same requirement, prefer fewer artifacts, state locations, concepts and operational steps. An addition explains the observed failure, its frequency and why prose, an existing check or deletion does not meet the goal. Plans and changes cite the architecture decisions they serve; one that serves none is a design choice the owner has not made. Correct but needless complexity prevents a positive review under `ADR-PUBLICATION-SCOPE-AND-COMPLEXITY-001`. Read a specification literally and implement the nouns and verbs it states; a marker or trigger it names is not a record, because an entity noun invites a store and lifecycle nobody specified. Simplification never deletes protected behavior or settles a material owner choice.

An agent works within its accepted source and test paths in the registered private checkout and preserves unrelated bytes. Native publication enforces file scope once by copying only those paths, checking current preimages and reading back the result. Private changes outside the accepted set are excluded and their filenames and mechanical line totals are reported in READY without blocking publication. Credential scanning examines the exact captured bytes selected for publication.

Claims reserve one exact next artifact and provide concurrency fencing; they do not assign continuing ownership of a work item or chain. Current writers validate their own domain conditions. Owner ordering operations check the actual initialized caller at the operation boundary. Agents use native publication and project finalization for Git effects; direct Git mutation remains forbidden by rule. Scope, review, tests, project ordering and attribution retain separate meanings.

A correction reconciles the smallest complete affected closure across formal requirements, work topology, implementation, tests, authored instructions, generators and consumers. Independent review investigates adjacent obligations beyond selected citations. A narrow passing result or structural linkage cannot hide a missing consumer, unresolved contradiction or untested required behavior (`REQ-GTKB-HARNESS-BASELINE-PROJECTION-CONTRACT-001`). `ADR-SPEC-COVERAGE-ARCHITECTURE-001` defines this investigation responsibility and the limits of the current discovery machinery.

## 1.4 Component responsibilities

The owner sets product direction, project ordering and human management choices. The domain authority exposes typed operations for specifications, projects, work items, tests, relationships and current history. Membase is the canonical knowledge store (`ADR-POSTGRESQL-AUTHORITY-SUBSTRATE-001`). The CLI and Home use the same supported operations.

The native bridge binds a receiving context to its actual native identity, application subject and PB or LO role. It coordinates authored messages, exact next-artifact claims, publication and independently reviewed project finalization. The Dispatcher selects eligible actions and registered harness/model pairs according to current configuration; agents author proposals, reports and verdicts (`SPEC-INTAKE-fee587`).

The context composer retrieves current assigned-work facts and the SAD sections selected for the receiving envelope. The projector derives harness-discoverable agent instructions and Skills from this Markdown source. Adapters transport work and collect execution observations using each harness's supported interfaces. They preserve the distinction between a requested model, an observed runtime selection and a provider-returned model.

The agent edits its private registered checkout. Publication copies only the accepted proposal and test paths, checks current claim, scope and canonical preimages, and reads back the result. The READY report exposes private edits outside those paths and mechanical change counts. Loyal Opposition investigates correctness and complexity independently before accepting work. These boundaries follow `ADR-PUBLICATION-SCOPE-AND-COMPLEXITY-001` and `ADR-SAD-SINGLE-AUTHORED-SOURCE-001`.

## 1.5 Architectural integration and acceptance

A complete change reconciles its affected canonical requirements, implementation, meaningful tests, agent instructions and human documentation. It preserves unrelated work and uses the accepted ending appropriate to the assigned subject. `ADR-ARTIFACT-ORIENTED-DEVELOPMENT-001` and `GOV-PROJECT-VERIFIED-COMPLETION-RETIREMENT-001` define the work-product and complete-project boundaries.

Acceptance claims name their actual scope. Source behavior, installed-package behavior, a native session, independent workflow review and a public fresh installation establish different facts. A requirement record or a discovered test supplies no executed result. A component result carries only while the inputs and behavior on which it depends remain applicable. Current program and release records state rollout progress; the SAD explains architecture.

## 2.1 Current authority and historical rationale

Current active formal records state requirements. Canonical domain services return current terminology (`GOV-GLOSSARY-AS-DA-READ-SURFACE-001`, `ADR-DA-READ-SURFACE-PLACEMENT-001`), configuration, project, membership, work-item, test and coordination facts. Clients use the typed CLI and service readers (`GOV-POSTGRESQL-LAN-AUTHORITY-SERVICE-001`). Historical deliberations, session logs and reports explain earlier reasoning or supply identified evidence; they confer no current authorization, role, claim, review result or completion (`GOV-COOPERATIVE-GOVERNANCE-AND-AUDITABILITY-001`).

Apply operative owner choices directly to the affected current source or requested action and preserve formal history (`GOV-09`, `GOV-ARTIFACT-ORIENTED-GOVERNANCE-001`). An existing direction needs no separate permission ledger (`SPEC-INTAKE-36ec00`), and an owner answer is never recorded outside its canonical source (`SPEC-INTAKE-e4d95b`). A program predicts and sequences an outcome; a project groups interdependent work that completes together. Revise the future plan when observations change its assumptions, compare predicted and actual outcomes, and improve the method. Completed activity belongs in evidence and history rather than an accumulating future plan. The `gtkb-program` skill carries the planning procedure.

Use historical material only for a named evidence, diagnosis or recovery purpose, identify its actual source and keep it after current instructions. A successor reconstructs the assignment from current domain state and its received transition. `DCL-CANONICAL-CARRIER-NONAUTHORITY-001` defines the boundary between operative records and informative carriers.

## 2.2 Source roles and precedence

Operative owner direction governs the assigned work and resolves conflicts with platform guidance. Apply a material change to the affected current canonical source. Active formal records in Membase define intent; canonical domain records define planning and coordination facts. Git establishes repository content, mode, commit identity and ancestry, and the registered remote supplies publication facts. A source registry identifies each source's responsibility without expanding it. `GOV-ARTIFACT-AUTHORITY-HIERARCHY-001` and `GOV-SOT-SINGLETON-001` define these source roles.

The SAD is the single authored source of agent-facing prose. Agent files and harness pointers are generated views of its sections; the human DOCX is a generated view of the whole SAD. Testable requirements and architecture decisions remain self-contained Membase records (`DCL-SPEC-DA-CITATION-MANDATORY-001`). Named skills carry CLI procedures. Bridge messages instruct a receiver at delivery, while subsequent actions use current attempt, scope and claim facts. Generated output, caches, notes and historical messages cannot override or complete an authoritative source (`GOV-HARNESS-NEUTRAL-BASELINE-001`).

Treat recall, intuition and previous conclusions as hypotheses. Test each material claim against the applicable current canonical source and actual work product. Read enough content, identity, version and lifecycle state to establish the fact needed at the decision boundary. A copied digest, timestamp or healthy label does not prove currentness. Missing, stale, inactive, ambiguous or contradictory required input identifies the affected decision and its correction route, terminology included (`DCL-CONCEPT-ON-CONTACT-001`); unrelated valid subjects remain available. `GOV-SOURCE-OF-TRUTH-FRESHNESS-001` defines current reads.

Correct conflict at the authored or canonical source rather than adding another editable description. Current code establishes observed or inspectable behavior; it does not silently settle contrary owner intent. Record the exact discrepancy and resolve its affected scope. Preserve actual formal and Git history while removing obsolete narrative from current prose.

## 2.3 Component and source map

The PostgreSQL kernel implements the typed domain and relationship tables, immutable native bindings, bridge attempts/items and claims, and record history. Native authority operations validate current application scope, relationships and expected versions. The bridge service validates message authorship, transitions, claims, publication and finalization. Worktree operations interpret reviewed paths within the project's registered repository; `ADR-APPLICATION-ISOLATION-CONTRACT-001` defines application scope and repository selection. Platform code, guidance and specifications assume no specific application (`DCL-PLATFORM-APPLICATION-NON-SPECIFICITY-001`).

The context composer joins current work, parent, formal, test and prerequisite facts with the receiving role/activity and selected SAD sections. Harness projection generates the common floor, Skills and discovery pointers from the same source. Dispatcher selection consumes current canonical work, registration, capabilities, policy and attributable observations. Execution adapters supply native/provider observations and actual owned-process ending.

Home is the standard human interface built on the integrated DeepSeek Harness SDK. It exposes supported setup and management operations using the selected installation's configuration. The dashboard is a linked service at its configured endpoint. Configuration and management requirements are resolved from current canonical records; an architecture description does not establish that every GUI journey has been qualified.

## 2.4 Project authorization and membership

Resolve `GOV-PROJECT-IMPLEMENTATION-AUTHORIZATION-001` through the selected installation. An execution project has the owner's ordering field `authorization`, with value `authorized` or `not authorized`; `ADR-PROJECT-AUTHORIZATION-EVENT-MODEL-001` makes that row field the only authorization. Programs sequence projects and carry no execution authorization. Each open work item has one execution-project parent (`DCL-WORK-ITEM-MUST-BELONG-TO-APPROVED-PROJECT-001`). The standing intake remains not authorized (`GOV-STANDING-BACKLOG-001`). A work item's legacy `approval_state` value, still visible in backlog records, grants and denies nothing.

The NEW admission checks the current parent's authorization (`DCL-PROJECT-AUTHORIZATION-OPERATION-TIME-ENFORCEMENT-001`). A later ordering change does not cancel an initiated chain. Independent review, current requirements, explicit application scope and the exact next-artifact claim continue to govern the assigned action.

An actual active-member-set change atomically deauthorizes every affected execution project; no-ops, stale requests, failures and nonmembership amendments preserve ordering (`DCL-PROJECT-AUTHORIZATION-EVENT-TRANSACTION-001`). Owner levers change authorization, move established members and create default-authorized execution projects. The authority operation checks the actual bound caller role at its effect boundary. Agents can create planning children with the explicit creation-only not-authorized value and amend ordinary nonmembership facts through supported writers; they retire open work only on an owner decision, a superseding artifact or completion evidence (`PB-STANDING-BACKLOG-CONTINUITY-001`). The project Skill supplies the exact command and readback procedure.

## 2.5 Owner choices in current records

Apply an owner choice to the affected current canonical record or authored source using its supported writer, expected version and readback (`GOV-SPEC-CAPTURE-TRANSPARENCY-001`), without asking again for an answer already given (`GOV-REQUIREMENTS-COLLECTION-HOOK-001`). The resulting current state directs later work. Purposeful historical retrieval can explain a decision or support diagnosis and recovery under `DCL-CANONICAL-CARRIER-NONAUTHORITY-001`.

The owner is the product decision maker. GT-KB formal-agent rules govern agents admitted to its workflow.

## 3.1 Architecture reference role

SAD sections explain current relationships and the reasons for the work process. Envelope composition selects them for the actual receiving role and activity alongside complete mandatory work, project, test, prerequisite and sender-critical formal content. The shared floor accompanies every envelope. A named excerpt assists understanding; it supplies no authority, permission, passing result or certificate of complete applicable closure. `ADR-SPEC-COVERAGE-ARCHITECTURE-001` distinguishes available discovery from a qualified full compiler.

Remove obsolete or misleading context at its source instead of adding an instruction to resist it (`DCL-INSTRUCTION-QUALITY-MEASUREMENT-001`). Give an agent the explanation it needs to act correctly in the section's agent-facing prose. Human reader notes explain an editorial choice, remain with the section and are stripped from every agent context and projection. Generated agent files direct edits to the SAD; the whole-SAD human DOCX retains visibly distinct notes and the Markdown source hash.

Finished-session review uses available observations to identify stale references, failed or repeated calls, unused output, duplicate reads, private out-of-scope edits and context size by source, and confirms reader-note exclusion. Loyal Opposition then searches for contradictions and violations of canon. Correct each defect in its authored source section and measure defects per finished session. `ADR-SAD-SINGLE-AUTHORED-SOURCE-001` defines this standing verification step.

## 3.2 Context composition

The sender identifies the critical knowledge for the successor while it has the work in context. It requests context for that successor's explicit receiving role and activity, adding selected section IDs and current formal IDs. The required shared floor, linked work/project/test and prerequisite facts remain included (`GOV-PROJECT-REQUIRES-LINKED-SPECIFICATIONS-001`). The sender then includes the informative result in the authored message; composition does not author a header, claim an action or deliver a verdict.

The receiver reconstructs its assignment from the delivered message and current canonical reads. Complete direct and sender-critical formal sources retain their typed closure. A selectively omitted current source is identified by its ID, version, lifecycle and ordinary retrieval route, and must be read before an applicable decision. Individually selected historical sources retain their explicit evidence, diagnosis or recovery purpose. Missing, inactive-required, stale or conflicting inputs produce the supported diagnostic and recovery route.

`ADR-SPEC-COVERAGE-ARCHITECTURE-001` defines the minimum reader's scope. Named excerpts are an investigation floor. `DCL-SPEC-RELEVANCE-CLOSURE-001` defines the separate complete relevance and closure duties. Receiving enough prose to begin an assignment does not establish that all applicable intersections have been discovered.

## 3.3 Semantic closure review

Prime Builder and Loyal Opposition independently investigate the affected canonical closure. They expand beyond stored citations when behavior, dependencies or cross-cutting rules indicate adjacent requirements. The review reconciles current formal intent, implementation, meaningful checks, authored instructions and documentation for that closure and removes obsolete references at their source (`ADR-OBSOLETE-REFERENCE-PURGE-OBLIGATION-001`).

The reviewer searches for disconfirming evidence and examines whether each mechanism is necessary under `ADR-PUBLICATION-SCOPE-AND-COMPLEXITY-001`. An omitted applicable requirement, missing required check, contradictory instruction or unnecessary complexity is a finding. Resolve it at the established authored or canonical source before accepting the affected work.

Because GT-KB succeeds when one agent's work item lets another agent produce specification-matching, test-passing work, both roles ask whether a choice leaves routine reconciliation, inspection, process memory or supervision with the owner, and say why and whether to automate, specify or decide it.

## 4.1 Separate implementation and review contexts

Prime Builder studies the assigned work, clarifies material uncertainty, plans and executes with confidence. It authors the proposal, implements an independently accepted proposal and reports the actual result in READY. Loyal Opposition independently critiques the proposal and verifies the work it did not author. The immutable receiving-context binding establishes the role; harnesses, models, providers, workstations and registrations remain role-neutral. `DCL-INIT-BOUND-SESSION-IDENTITY-001` and `DCL-SESSION-ROLE-RESOLUTION-001` define identity and role resolution.

For every new or changed major behavior, contract, invariant and consequential
choice, Prime Builder keeps compact canon provenance in the owned code or
artifact: governing record ID, applicable clause and version basis, with a brief
obligation or rationale. An explicit common module/class scope may cover its
helpers. Distinguish canonical requirements from engineering choices and
unresolved assumptions; comments are pointers, never authority. Maintain affected
citations, rationale and tests with the change. Loyal Opposition treats each as
a potentially erroneous claim, checks current sources and actual behavior, and
investigates omitted requirements. Use gtkb-bridge for authoring, gtkb-verify for
verification and gtkb-loyal-opposition-report for ordinary-work observation.

Loyal Opposition begins with complexity, then correctness. It asks whether each added branch, state location, cache, table, hook, gate or record should exist and requires a simpler design when one meets the same goal. When a change answers a failing check by building around it, it asks whether the check itself is too rigid. A preference without evidence and tradeoff is not a finding. It searches for errors and disconfirming evidence against current requirements and exact artifacts. The work bears the burden of proof (`DCL-CROSS-HARNESS-ENFORCEMENT-001`). A missing required check is a finding; agreement, confidence and the absence of an obvious defect do not establish verification.

Formal review requires a distinct eligible context and a different canonical model identity established from accepted execution observations. Requested selectors, authored headers and harness or provider names do not supply that proof. Cursor's exact mapped runtime selection label observed at initialization is the session's attribution and review identity while it runs, with the underlying backend identity unknown. Completion confirms that label and refuses a changed value; a session that later fails keeps attribution on what it delivered. The completed-session join applies to qualification evidence only. Automatic, ambiguous, missing, unmapped or fuzzily inferred labels receive no model credit. `ADR-CURSOR-RUNTIME-SELECTION-EVIDENCE-001` defines that bounded observation and its disclosure.

Prime Builder does not author formal review verdicts or make a project commit. Loyal Opposition does not perform the mutation it is assigned to verify or certify its own authored correction. GO follows complete proposal review; VERIFIED follows independent review of the exact final work and remains distinct from commitment. Only an eligible Loyal Opposition context performs the one native complete-project commit after every member has current independent verification. A fresh eligible context recovers finalization from current canonical review and actual Git facts. `GOV-PROJECT-VERIFIED-COMPLETION-RETIREMENT-001` defines that ending and recovery.

Formal GT-KB contexts exchange assignments and authored artifacts through canonical CLI, Bridge and Dispatcher surfaces. They reconstruct current work without another harness's files, memory or live runtime state. `GOV-HARNESS-ISOLATION-001` and `DCL-HARNESS-DISPATCH-ISOLATION-INVARIANT-001` define that separation.

## 4.2 Independent test selection

Loyal Opposition selects meaningful checks from current proposal intent, applicable requirements and affected risks. The author-selected test list is a starting point, not a coverage ceiling (`GOV-CROSS-CUTTING-REQUIREMENTS-MECHANICAL-ENFORCEMENT-001`). Structural assertions establish only their inspected structure; behavioral or semantic requirements receive the checks needed to substantiate them under `DCL-VERIFIED-SPEC-DERIVED-TESTING-MANDATORY-001`, primarily outside-in through the observable interface (`GOV-19`).

Review records the current requirement/consumer, selected population, executed
outcomes and evidence limits. Before attributing a test failure to the product,
judge its technical/usability purpose, values, oracle, fixture, selected artifact,
environment and actual ending. Distinguish product defect, test defect, both or
unresolved evidence; preserve the original result. A flawed test is not product
acceptance. A bound ending alone is not a demonstrated requirement violation.

Reuse accepted component evidence when relevant behavior, inputs and predicates
remain applicable. A hash change establishes changed provenance, not changed
behavior everywhere. Each repeat names its changed behavior or concrete
unresolved question and sufficient next observation. Maintain current runtime
qualification/admission until a coherent reviewed implementation changes reuse.
Choose the smallest sufficient discriminating check for the current claim or
unresolved cause and return actionable evidence. Broaden when that result or an
uncovered requirement justifies the next check.

`ADR-SPEC-COVERAGE-ARCHITECTURE-001` supplies the coverage contract; the
verification Skill supplies the procedure.

## 4.3 Verification and project finalization

VERIFIED records independent review of exact work. Reviewed artifact identity includes Git mode and object ID for each concrete path, with explicit absence for deletion. Every project member must be independently verified in the complete final form before the verifying Loyal Opposition creates one project commit through normal hooks. `GOV-PROJECT-VERIFIED-COMPLETION-RETIREMENT-001`, `ADR-GOVERNED-TWO-TIER-GIT-LIFECYCLE-001` and `REQ-GTKB-GOVERNED-GIT-LIFECYCLE-001` govern this boundary.

The candidate is created in the reviewer's checkout. The service compares it with current complete-project reviews before integrating that same commit and confirming canonical terminal state. A private candidate alone is not an integrated project result (`DCL-GIT-BRANCH-BINDING-PROMOTION-001`). The successful integrated commit establishes activation and member terminality; canonical state mirrors that Git fact.

Changed reviewed bytes return only the affected members for fresh verification. Material formal-intent change before commitment requires the supported fresh proposal/review/implementation attempt without inherited GO. A failed commit preserves its typed cause and reviewed state. An uncertain acknowledgement first reconciles the actual candidate and canonical confirmation through the normal recovery route, without inventing a verdict or resetting history.

## 4.4 Program design and execution

Design from the operational goal down to a component model, explicit contracts
and global happy-path cases. A global case states initial conditions, the useful
request, cross-component interactions, observable result and proper ending.
Acceptance derives from current requirements, not a preferred implementation.

Components are peer deliverables with peer development/testing sub-programs.
Choose coherent responsibilities and narrow knowledge needs; keep each
cross-cutting invariant under clear ownership. Loose coupling reduces dependence
on other internals; more modules or contexts do not establish independence.
Define relevant input/output, error, lifecycle, state/resource, dependency and
operating contracts. Develop internals and their harness/test plan together.
Integration is a dedicated workstream consuming substantially complete
components capable of their part in the global happy path with applicable
component acceptance. Early interface experiments retain their learning scope.

The durable hierarchy is goal, component sub-program, cohesive project and
testable work-item contribution. A session is an execution attempt, not a new
scope or definition of completion. Preserve parent obligations across contexts
and separate implementation from independent review. Map logical groupings to
supported current records; do not invent record kinds or lifecycle operations.
Authorization/publication remain those of the actual operating context.

Use gtkb-program-design to construct or materially re-plan the model and
gtkb-program to run it. Explicitly select current instructions at assignment;
discovery alone is not receipt. Native agents use current canonical writers,
binding, Dispatcher and Bridge. An expressly authorized externally managed
program uses its approved records/tools without pretending to have native
binding or gaining native effect rights. Harness planning defaults supply
neither this methodology nor the program's acceptance.

Choose ready assignments from actual dependencies, usable input versions,
functional output and authority/resources. Parallelize independent component and
test work under actual isolation/capacity. Fresh context is not filesystem
isolation. Supply objective, current inputs, owned effects, acceptance/evidence
and a return/continuation condition. The runner retains accountability for the
complete goal; delegation does not qualify an output.

Judge adverse tests under section 4.2 before repair. Repeats need changed behavior
or a concrete unresolved question. Preserve applicable evidence and current
runtime admission until a coherent reviewed implementation changes it. Do not
shrink acceptance because a context ends, a test is difficult or a forecast fails.

At progress review judge architecture/contracts, executable assignments,
dependency/resources, test/integration efficiency, release convergence and
instruction effectiveness. Use actual populations/intervals and state unknowns.
Trace concrete flaws, consequences, responsibility, smallest correction and
subsequent ordinary behavior. Distinguish proposed, applied, delivered and
effective guidance. Incorporate supported lessons into authored skills/current
plans. Artifact volume is not goal achievement. Do not impose a separate
mandatory methodology-proof campaign.

## 5.1 Authored headers and routing

`DCL-LAYERED-MESSAGE-ENVELOPE-001` defines the ordered message knowledge: peer-review protocol, receiving PB/LO role, application subject, activity and sender additions. The receiving init binds subject and role; open selects the transient activity. Authored provenance identifies the sender separately.

The bridge writer validates the complete receiver-directed header, typed routing and authored content under `ADR-BRIDGE-ARTIFACT-HEAD-ENVELOPE-001`. A dispatchable message supplies its init marker, activity and one canonical status in the first three nonblank lines. A non-dispatchable message omits receiver init/open/role fields. The bridge Skill supplies the exact grammar and status-specific fields.

The message carries the instruction received for that action. Current attempt state and the exact artifact claim govern later effects. `GOV-FILE-BRIDGE-AUTHORITY-001` and `DCL-BRIDGE-CLAIM-LIFECYCLE-001` define this coordination; an envelope is composed message knowledge rather than a mutable stored session object.

## 5.2 Twelve-status lifecycle

The native bridge uses the canonical twelve statuses of `SPEC-BRIDGE-STATUS-PHASE-DISTINCT-001`. Prime Builder proposes NEW or REVISED and reports READY after GO. Loyal Opposition reviews proposals with GO or NO-GO and reports with VERIFIED or NOT-READY. Rejection returns the assigned correction or revision action through the current transition contract. The bridge Skill explains the remaining statuses and exact delivery commands; the canonical vocabulary and transition reader determine which successor is legal.

A claim reserves one exact next-artifact slot from its current predecessor, version, intended status and fence for 600 seconds. Delivery rechecks those facts, publishes the agent-authored bytes and consumes the claim atomically. An idempotent retry preserves the original expiry. A later eligible context obtains its own action claim; authorship does not reserve the work item or its chain. Reconcile uncertain delivery from the same content and fence before another attempt.

## 6.1 Immutable native session binding

The exact received `::init <application-ID> <pb|lo>` marker creates or retrieves one immutable binding for the actual harness-native context. The selected installation resolves the application subject and stores its registered spelling. A requested role, harness name, model, prior author or environment default cannot substitute for this marker or the actual context identity. `GOV-SESSION-SELF-INITIALIZATION-001`, `SPEC-CANONICAL-INIT-KEYWORD-SYNTAX-001` and `DCL-INIT-BOUND-SESSION-IDENTITY-001` define initialization, marker syntax and retry.

Identical initialization is idempotent. Conflicting subject or role refuses without rebinding. Ending a worker, closing an activity or removing a checkout does not delete this attribution or authorize another role for the same context. Execution enrollment by itself is not initialization or an agent role.

Startup context returns the current binding, required current formal records and the selected SAD floor. It establishes no activity, assignment, service activation or completed host startup. Unknown host capabilities and unavailable token measurements remain unknown. After reading the binding, the receiving context discloses the startup facts `PB-SESSION-STARTUP-GOVERNANCE-DISCLOSURE-001` requires, and any measured token consumption with the context-reduction options of `DCL-SESSION-STARTUP-TOKEN-BUDGET-001`. It preserves the owner's input and resolves missing inputs before dependent work. The bridge Skill supplies the exact binding and context-read procedure; a launcher that already returned the exact binding does not initialize it again.

When the current project selects a registered application repository, startup offers the next missing core-specification question through the `gtkb-spec-intake` Skill (`SPEC-CORE-INTAKE-001`). A headless context reports the returned question without waiting for an answer (`DCL-CORE-INTAKE-001`).

## 6.2 Transient activity and bounded operations

An explicitly delivered `::open` selects build, test, spec, deliberation, project or ops for the current context. The activity is transient and separate from immutable identity. It is not inferred from a role, a work item or a queue. Close, wrap and termination clear the selection without changing the binding.

Named service and control operations use their existing operation boundary. A Prime Builder operational bound identifies the operation, targets, preconditions, permitted effects, expiry, verification, containment and rollback. The native `gt bridge check-operation` route checks that actual named operation in the selected installation. It does not predict a shell command's effects or intercept private file editing.

Service startup, readiness, running ownership and cleanup are separate observations. Reports retain the original deadline, the processes and resources actually owned, and the actual ending. A listener disappearing later is not evidence that an earlier owned process or resource ended as required.

## 6.3 Ephemeral scratch space

The service derives this context's disposable scratch from its immutable binding under the selected installation's scratchpad. It returns that owned directory with the registered checkout; scratch is neither canonical knowledge nor a bridge artifact or project-commit input.

An explicit authorized close or wrap uses the native scratch-teardown operation. The operation refuses a redirected scratch root, leaves the binding and sibling contexts intact, and reports each removal or survivor. A host deletion refusal produces a partial result; absence is reported as absence. No session-end hook supplies an automatic cleanup result.

Canonical records and actual work product preserve continuity (`ADR-0001`). Harness-local memory, planning files and runtime scratch may help investigation but do not establish current requirements, authorization, review or passing evidence.

Every native work session preserves its complete transcript and tool log in the run's declared evidence custody after the original ending. Preserve that evidence before cleanup deletes its harness home. A missing log is a measurement gap, not a completed post-mortem. Retained logs support diagnosis and measurement, not canonical authority.

Agent contexts never read, search, list, print or write credential material, such as credential files, environment listings or secret-named variables, because whatever an agent reads reaches its model provider; the owner handles credentials in their own terminal. Agents use environment references and supported canonical readers, so credential values never enter source, reports or retained evidence (`GOV-ENV-LOCAL-AUTHORITY-001`).

## 7.1 Coordination responsibilities

Dispatcher separates selection and custody from agent authorship under `ADR-DISPATCHER-COMPLEX-CLI-001`. It reads current work and project facts, registered harness routes, policy and attributable observations; agents provide proposals, reports and verdicts. A transport delivers an assignment and its envelope, not a decision or role of its own (`ADR-DISPATCHER-WORKER-CONTEXT-FACADE-001`).

The configured manual mode leaves selection with the owner. Shadow suggestions also leave the choice with the owner. Automatic selection requires the separately qualified and activated capability. The owner changes Dispatcher configuration and mode only through `gt dispatcher set-config` and `gt dispatcher mode`, with expected version and readback; agents do not change them (`DCL-DISPATCHER-CONFIG-CLI-ONLY-001`). Source code or a registered route alone supplies no activation or current native qualification. External delegated coordination can transport an existing owner assignment through the supported manual route without becoming a GT-KB agent.

The receiving context reconstructs the assignment through its exact message and current canonical reads (`DCL-DISPATCHER-WORKER-SAFE-PACKET-CONTRACT-001`). Neither a cached queue nor the previous author owns its next action. Current capacity, route eligibility and original resource ending constrain a launch or retry.

## 7.2 Selection order

Selection applies current hard eligibility before preference ordering. NEW requires the current execution parent's authorization; deauthorization does not cancel an already initiated chain. Current dependencies and readiness determine eligibility. An uncommitted VERIFIED message cannot substitute for a required complete project commit.

Among eligible actions, the configured canonical priority and FIFO order apply. Harness/model selection follows the current policy, qualified capability, budget and independent-review constraints. A preference or soft score cannot defeat a hard exclusion.

Reachability, latency, cost and efficacy are observations with scope, time and freshness. Missing measurements follow the configured unknown-state policy rather than invented success. Ordered alternatives obey the user's fallback policy. If no acceptable route exists, report unavailable or defer. Before another attempt after an uncertain launch, reconcile the actual binding, claim and owned process state.

## 7.3 Exact artifact claims and recovery

A canonical bridge claim reserves the exact next document/version slot from its current predecessor, intended status, bound author and monotonic fence. `DCL-BRIDGE-CLAIM-LIFECYCLE-001` specifies the fixed 600-second expiry, exact retry and atomic consumption. A retry preserves its original expiry; there is no renewal or continuing ownership of the work item or chain.

The claim arbitrates the accepted proposal paths and test targets, including overlapping implementation and test work. Its current-scope check rejects moved parents or changed required work/formal snapshots rather than loading an obsolete checkout. Separate Git and named operational boundaries do not extend the artifact claim across messages.

Delivery rechecks the exact predecessor, live fence, author and intended successor, publishes the authored bytes and consumes the claim. Reconcile uncertain acknowledgement using the same content and fence. Release compares the current context and fence. An expired reservation needs a new exact claim, not an inferred grace period. Recovery claims name the actual current state; a historical result supplies no new effect right.

## 8.1 Publication scope and registered workspaces

The accepted proposal names the source paths and test targets to deliver. The context edits its registered private checkout, preserves unrelated work and uses the service-returned artifact preimages. Agent prose guides private editing; native publication is the deterministic enforcement point under `ADR-PUBLICATION-SCOPE-AND-COMPLEXITY-001`.

Publication captures only the accepted paths, rechecks the private source, current claim/scope and canonical preimages, scans the captured published bytes for credentials, applies the bounded change and reads back. A changed preimage or redirected artifact refuses. Private edits outside the accepted set are not copied into canonical work. Publication and READY each take their own filename-only observation; changed out-of-scope names are visible to the reviewer without blocking publication. Mechanical added/removed line totals cover accepted published paths, with binary totals explicitly unknown.

The selected installation and registered independent application repository define the work boundary under `ADR-ISOLATION-APPLICATION-PLACEMENT-001`. A guessed sibling or archived root supplies none. Declared external harness runtimes are invocation dependencies (`DCL-PROJECT-ROOT-BOUNDARY-EXTERNAL-HARNESS-EXEC-EXCEPTION-001`), not permission to treat private harness state or memory as canonical evidence.

Applications retain their own scope and repository, and an application root holds only artifacts its application registry classifies (`DCL-APP-ROOT-MINIMIZATION-001`); a platform change does not retire their requirements. Unqualified GT-KB CLI, CI, source, release and verification references mean the platform.

## 8.2 Exact edits and encoding

A source edit preserves unrelated bytes, meaningful formatting, encoding and line endings. The accepted artifact list includes explicit deletion where intended, and the registry declaration when the work creates, moves, renames, removes or changes the coverage of a load-bearing platform artifact (`GOV-PLATFORM-SOT-REGISTRY-001`, `DCL-ARTIFACT-REGISTRY-MUTATION-AUTHORIZATION-001`). The published and independently reviewed identity is each concrete path's actual Git mode and object ID, not a description of the edit or a count of tests.

Publication scans exactly the captured bytes it will copy using the canonical credential patterns and reviewed fixed fixture exclusions. Credential text is not exported in refusal reports.

Guidance is authored in the SAD. Publication and install project agent outputs automatically. `gt harness project <harness> --dry-run` previews the derived content and removals without writing. Generated Skills under `.agents/skills` and native harness discovery outputs stay uncommitted; direct edits are overwritten and never published. Each generated prose file identifies its SAD path and source section, with the contact header following a Skill's complete YAML front matter.

The committed `AGENTS.md`, root pointers and human DOCX are derived artifacts. Publication compares the generated floor and pointers with their committed outputs, and the DOCX's recorded source hash with the current SAD. A mismatch refuses and names the source section to correct; changing a derived file does not create another authored source.

## 8.3 Native CLI interfaces

The native CLI exposes supported typed domain, context, bridge, project, specification, test, harness and service operations. Its current registered command grammar determines accepted arguments. Named Skill sections supply the procedure and readback for the selected action; this architecture does not maintain a second static command inventory.

Use the normal reader for current facts, the current expected version for mutation and the returned recovery route for failure. A refusal or unavailable service is not a completed action. Native worktree/publication and complete-project lifecycle operations perform their Git effects; direct Git mutation remains forbidden by rule. Named operation checks apply at their own actual service/control boundary.

Installed interface behavior requires evidence from that installation, separate from a source declaration.

## 9.1 Supported assertions

`gt assert` evaluates the supported structured assertions for the selected current formal scope. A file, function or registered test declaration proves inspected structure only. It does not show that the test ran, that a behavioral requirement holds or that the affected semantic closure is complete.

Select behavioral and semantic checks from current requirements, the proposed change and concrete risks. Record the collected population, actual outcomes, failures, omissions and limits. `ADR-SPEC-COVERAGE-ARCHITECTURE-001` defines coverage interpretation; the verification Skill supplies the independent review procedure.

## 9.2 Distinct evidence levels

Source qualification, installed-product qualification, a native session, independent agent workflow and fresh-install release checks prove different scopes. Passing one does not silently substitute for another. Carry an accepted component only when its relevant inputs and tested predicates remain applicable; repeat affected checks when behavior changes or a concrete unresolved concern requires it.

A genuine workflow uses the assigned substantive subject and reaches the appropriate accepted ending. Work-product implementation needs independent different-model review and the complete project commit. Canonical-data correction needs normal version checks, history/readback and independent factual acceptance. Authored reports, configured model names and structural discovery do not fabricate those results.

Model comparison uses qualified canonical identities established from accepted observed sources. For Cursor, the exact mapped initialization selection is the session's attribution and review identity while it runs; completion confirms it and refuses a changed value, and a session that later fails keeps attribution on what it delivered. The completed-session join applies to qualification evidence only, joining that selection to the same successfully completed session, exit, EOF and drain. It is explicitly runtime-selection evidence with backend model unknown, as `ADR-CURSOR-RUNTIME-SELECTION-EVIDENCE-001` defines. An ambiguous, automatic, missing, unmapped or fuzzily inferred label supplies no model credit. A requested selector or authored header never replaces the observation.

## 9.3 Release and document checks

A release uses the exact qualified artifact through the normal package, installation and origin/readback path. Reports name the current source, artifact, installed origin, actual original ending and remaining acceptance. Do not call a component pass a product release or infer an ending from current absence. A deployment to any environment starts only on the owner's explicit approval given immediately before it.

The human whole-SAD DOCX is generated from the sole Markdown source, includes visibly distinct reader notes and records its Markdown SHA256. Its currentness check compares that source hash. Agent outputs omit reader notes and are reproduced by the existing projector check. Composer reads Markdown, not Word XML.

Reconcile source, canonical citations and the rendered human result before document publication. Prose changes require the relevant rendering/projection validation; changed behavior also requires its affected qualification. No document, installed artifact or native route is accepted merely because this SAD describes it.

## 10.1 Role-neutral registration and model evidence

Harness and installation registration describe the current invocation, supported capabilities and configured model routes (`GOV-HARNESS-ONBOARDING-CONTRACT-001`). They do not assign PB or LO (`ADR-SINGLE-HARNESS-OPERATING-MODE-001`). The receiving marker and immutable binding supply role, while qualified policy determines route eligibility. File presence or a registry flag does not show a capability ran.

Model identity and route qualification do not establish reliable instruction
following. Assess suitability for the actual role and delivered guidance from
observed behavior; use the existing program and review procedures to distinguish
instruction, delivery, feasibility and execution failures.

Formal delivery uses protected observations attributable to the exact native context. An accepted provider-response model and a Cursor runtime selection have distinct evidence sources. When a declared dispatch selector differs from an observed Cursor label, the versioned mapping may name one exact recognized label; it does not guess aliases, model families or automatic selection. The underlying Cursor provider model remains unknown.

GO and VERIFIED compare the canonical model identities established by the qualified receipts and current/historical joins. A missing or mismatched mapping supplies no canonical model receipt or current effect credit. Successful physical cleanup retains its own terminal, drain and ownership predicates and is not made dependent on receiving model credit. API/CLI/schema round trips retain the mapping; an existing observation row discloses runtime selection and backend unknown without implying a dedicated mapping editor.

## 10.2 Measured resource behavior

Report caching, quotas, costs, latency, context size and efficacy only to the extent actual observations establish them. Retain the measured scope, source, time, freshness and unknowns. The configured budget and original deadline bound work; a timeout does not by itself identify its cause or justify a larger allowance.

Context composition serves the current required facts and selected agent-facing SAD sections. Skills are loaded for their procedures rather than copying the whole prose corpus into every envelope. Reader notes remain absent. Prefer targeted current retrieval and progressive disclosure; measure delivered context by source where available. The architecture claims no savings or complete relevance compiler from section selection alone.

## 11.1 Current sources and purposeful history

Current native records, versioned canonical history, authored SAD and actual work product have distinct roles. A current instruction is resolved through its established source. Historical material is selected for an explicit evidence, diagnosis or recovery purpose and retains its identity and provenance; it is not a default or a substitute for current state.

Refresh an affected source directly when an owner decision or demonstrated contradiction changes the contract. Preserve meaningful predecessor evidence without copying stale paths, commands, implementation plans or issue status into current guidance. Rollout progress and remaining work live in current canonical program, project and work-item records.

## 11.2 Defects and source correction

A defect report names the observed behavior, evidence, practical impact and smallest concrete correction. It distinguishes an actual failure from a risk, missing measurement or inferred cause.

Inspect every tool, Skill and CLI result for correctness, completeness and fitness, including an apparent success. Record a defect, gap or recurring procedural friction where it is found and before any workaround, because an unrecorded workaround leaves the next context to rediscover it at full cost: a reproducible defect becomes a work item through `gtkb-work-item` in `PROJECT-GTKB-NEW-WORK-INTAKE` (`GOV-RELIABILITY-FAST-LANE-001`), and an uncertain finding or material owner choice becomes an ADVISORY. Recording grants no approval to repair; disclose any workaround used, then continue the assigned target.

Review necessity before complexity is added. Name mechanical lines added/removed and new tables, hooks, gates or records; tie each mechanism to the architecture decision it serves. Prefer deletion, clearer prose or an existing deterministic boundary, including a deterministic service for repetitive, fully specified procedure (`GOV-DETERMINISTIC-SERVICES-PRINCIPLE-001`), over another copied source, cache, receipt or prediction layer. `ADR-PUBLICATION-SCOPE-AND-COMPLEXITY-001` supplies this review duty. Name hook-triggered and event-shaped concepts for when they fire, such as `init-trigger`, `on-init` or `open-hook`. When an entity noun stands for a triggered concept, file the mismatch through the current backlog writer (`gtkb-work-item`) rather than extend the noun with fields, records or lifecycle; filing does not require a separate permission record (`GOV-ARTIFACT-ORIENTED-GOVERNANCE-001`). Naming is a judgment about the actual behavior; a structural assertion cannot establish complete conformance.

A foundational defect that prevents its own ordinary repair follows `GOV-GTKB-EMERGENCY-BOOTSTRAP-001`. The owner gives the concrete bounded repair direction through the existing formal, project and work-item scope. A defective gate is disclosed and corrected, not silently treated as current canon. Use ops for live operations and obtain independent verification by a different context. Preserve credentials, path containment, foreign bytes and Git history. If the canonical writer is broken, restore it under that direction and then write the actual facts through the restored interface. A restored-route component test does not qualify an actual emergency operation. Ordinary friction, expired claims and unavailable reviewers do not create an emergency exception or a second permission ledger.

A finished-session post-mortem combines a deterministic pass over actual transcript/tool evidence with Loyal Opposition reading. Its six guidance-defect categories are stale references, contradictions between fragments, missing needed guidance, erroneous tool use that adds unneeded or obsolete content, erroneous artifacts and violations of canon. The existing wrap and Loyal Opposition report Skills supply the procedure, including failed or repeated calls, unused output, duplicate reads, scope drift, context size, writes to projections and reader-note leakage. Compare defects per finished session before and after a prose correction; correct each defect at its source.

Process outcomes use the current work-item records, receipts and Git evidence from the native runs. Report the share of work items that another agent takes to VERIFIED with passing tests and no owner correction; Loyal Opposition send-backs and their causes; defects found after VERIFIED; owner interventions and active owner time; measured cost and elapsed time per VERIFIED item; and forecast accuracy against actuals. These describe GT-KB's own process, separately from comparative efficacy. Missing inputs or measures remain unknown; a report does not fabricate an ending, passing result or measurement.
