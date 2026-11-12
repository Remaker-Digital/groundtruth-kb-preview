from __future__ import annotations
from collections.abc import Callable, Coroutine
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
import re
from typing import Annotated, Any, AsyncContextManager, Literal
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import RequestResponseEndpoint
from gtkb_protocol import FormalLinkIdentifier, ProtocolError as PostgresKernelError, canonical_json_bytes, parse_json_bytes
from .domain import (
    AuthorityService, DependencyMutation, HarnessMutation, Identifier,
    MembershipMove, MembershipScopeMutation, ProgramRevision, ProjectAuthorizationChange,
    ProjectFormalLinkMutation, ProjectMutation, ProjectRetirement, SpecMutation, TermMutation,
    TestMutation, TestPhaseMutation, TestPlanMutation, WorkItemMutation, WorkItemRetirement,
)
NativeRoutes = Callable[[FastAPI, AuthorityService, Path], AsyncContextManager[None]]
Domain = Literal[
    "specifications",
    "tests",
    "projects",
    "project-memberships",
    "work-items",
    "test-plans",
    "test-phases",
    "project-dependencies",
    "project-formal-links",
    "terms",
    "harnesses",
    "deliberations",
]


class CanonicalJSONResponse(Response):
    media_type = "application/json"

    def render(self, content: object) -> bytes:
        return canonical_json_bytes(content)


class CanonicalRequest(Request):
    async def json(self) -> Any:
        return parse_json_bytes(await self.body())


class CanonicalRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def canonical_request(request: Request) -> Response:
            try:
                return await handler(CanonicalRequest(request.scope, request.receive))
            except HTTPException as error:
                if isinstance(error.__cause__, PostgresKernelError):
                    raise error.__cause__
                raise

        return canonical_request


def _result(value: object) -> Response:
    # Avoid FastAPI's float conversion of arbitrary-precision canonical JSON.
    return CanonicalJSONResponse(value)


def _query_fields(request: Request, allowed: set[str]) -> None:
    if set(request.query_params) - allowed or len(request.query_params.multi_items()) != len(request.query_params):
        raise PostgresKernelError("invalid_query", "Unknown or repeated query fields are not accepted")




def _native_path(path: str) -> bool:
    path = path.rstrip('/')
    prefixes = ('/v1/dispatcher', '/v1/sessions', '/v1/bridge')
    return any(path == p or path.startswith(p+'/') for p in prefixes) or re.fullmatch(
        r'/v1/projects/[^/]+/(commit|prepare-commit|check-commit|confirm-commit|commit-failed)|/v1/work-items/[^/]+/(context|readiness)', path
    ) is not None


def create_authority_app(service: AuthorityService, *, project_root: Path, native_routes: NativeRoutes | None = None) -> FastAPI:
    if project_root != service.kernel.project_root:
        raise PostgresKernelError('invalid_postgresql_config', 'Select the same explicit authority project root')
    native_owner = None
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with AsyncExitStack() as stack:
            stack.push_async_callback(run_in_threadpool, service.close)
            if native_owner is not None:
                await stack.enter_async_context(native_owner)
            yield
    app = FastAPI(title='GT-KB authority', version='0.1.0', docs_url=None, redoc_url=None, lifespan=lifespan)
    app.router.route_class = CanonicalRoute
    if native_routes is not None:
        native_owner = native_routes(app, service, project_root)

    @app.middleware('http')
    async def local_cli_boundary(request: Request, call_next: RequestResponseEndpoint) -> Response:
        state = service.lifecycle_state()['state']
        if state != 'open':
            await request.body()
            error = PostgresKernelError('authority_'+state, 'Authority admission is '+state)
            return CanonicalJSONResponse(error.to_json_dict(), status_code=503)
        if native_routes is None and request.method in {'GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'} and _native_path(request.scope['path']):
            await request.body()
            error = PostgresKernelError('capability_unavailable','The required native or context capability is unavailable', details={'capability':'native'})
            return CanonicalJSONResponse(error.to_json_dict(), status_code=503)
        if request.headers.get('origin') is not None:
            return CanonicalJSONResponse({'code':'browser_origin_refused','message':'Use the GT-KB CLI'}, status_code=403)
        if request.method in {'POST','PUT','PATCH'} and request.headers.get('content-type','').split(';',1)[0].strip() != 'application/json':
            return CanonicalJSONResponse({'code':'json_required','message':'A JSON domain request is required'},status_code=415)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(PostgresKernelError)
    async def domain_error(_request: Request, error: PostgresKernelError) -> Response:
        if error.code == 'not_found':
            status = 404
        elif error.code in {'cas_conflict','retryable_conflict'}:
            status = 409
        elif error.code in {'postgres_unavailable','postgres_operation_failed','operational_policy_unavailable','capability_unavailable','authority_closing','authority_closed','authority_ending_unresolved'}:
            status = 503
        else:
            status = 422
        return CanonicalJSONResponse(error.to_json_dict(), status_code=status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, error: RequestValidationError) -> Response:
        # Validation diagnostics identify fields but do not echo submitted bodies.
        return CanonicalJSONResponse(
            {
                "code": "invalid_request",
                "message": "Request does not match the domain contract",
                "fields": [{"location": list(item["loc"]), "type": item["type"]} for item in error.errors()],
            },
            status_code=422,
        )

    @app.get("/v1/status")
    def status() -> Response:
        return _result(service.kernel.status())
    @app.get("/v1/registry/path-observations")
    def registry_path_observations() -> Response:
        return _result(service.registry_path_observations())

    @app.get("/v1/specifications/snapshot")
    def specification_snapshot(request: Request) -> Response:
        _query_fields(request, set())
        return _result(service.specification_snapshot())
    @app.get("/v1/authority/resolve")
    def resolve_authority(
        request: Request, subject: Annotated[str, Query(min_length=1)], scope: str | None = None
    ) -> Response:
        _query_fields(request, {"subject", "scope"})
        return _result(service.resolve_authority(subject, scope=scope))

    @app.get("/v1/authority/status")
    def authority_status(request: Request, scope: str | None = None) -> Response:
        _query_fields(request, {"scope"})
        return _result(service.authority_status(scope=scope))

    @app.get("/v1/{domain}")
    def list_records(
        request: Request,
        domain: Domain,
        limit: Annotated[int, Query(ge=1, le=1000)],
        after: str | None = None,
        search: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        priority: str | None = None,
        spec_id: str | None = None,
        plan_id: str | None = None,
        resolution_status: str | None = None,
        component: str | None = None,
        test_type: str | None = None,
        parent_project_id: str | None = None,
        repository_ref: str | None = None,
        project_id: str | None = None,
        application_scope: str | None = None,
        dependent_project_id: str | None = None,
        prerequisite_project_id: str | None = None,
        affected_gate: str | None = None,
        lifecycle_status: str | None = None,
        authority_level: str | None = None,
        scope: str | None = None,
        source_type: str | None = None,
        work_item_id: str | None = None,
        artifact_type: str | None = None,
    ) -> Response:
        accepted = {
            "after",
            "limit",
            "search",
            "status",
            "kind",
            "priority",
            "spec_id",
            "plan_id",
            "resolution_status",
            "component",
            "test_type",
            "parent_project_id",
            "repository_ref",
            "project_id",
            "application_scope",
            "dependent_project_id",
            "prerequisite_project_id",
            "affected_gate",
            "lifecycle_status",
            "authority_level",
            "scope",
            "source_type",
            "work_item_id",
            "artifact_type",
        }
        _query_fields(request, accepted)
        filters = {
            key: value
            for key, value in {
                "status": status,
                "kind": kind,
                "priority": priority,
                "spec_id": spec_id,
                "plan_id": plan_id,
                "resolution_status": resolution_status,
                "component": component,
                "test_type": test_type,
                "parent_project_id": parent_project_id,
                "repository_ref": repository_ref,
                "project_id": project_id,
                "application_scope": application_scope,
                "dependent_project_id": dependent_project_id,
                "prerequisite_project_id": prerequisite_project_id,
                "affected_gate": affected_gate,
                "lifecycle_status": lifecycle_status,
                "authority_level": authority_level,
                "scope": scope,
                "source_type": source_type,
                "work_item_id": work_item_id,
                "artifact_type": artifact_type,
            }.items()
            if value is not None
        }
        return _result(service.list_records(domain, filters=filters, after=after, limit=limit, search=search))
    @app.get("/v1/project-formal-links/{record_id}/history")
    def project_formal_link_history(record_id: FormalLinkIdentifier) -> Response:
        return _result(service.history("project-formal-links", record_id))

    @app.get("/v1/project-formal-links/{record_id}")
    def show_project_formal_link(record_id: FormalLinkIdentifier) -> Response:
        return _result(service.show("project-formal-links", record_id))

    @app.get("/v1/{domain}/{record_id}/history")
    def history(domain: Domain, record_id: Identifier) -> Response:
        return _result(service.history(domain, record_id))

    @app.get("/v1/{domain}/{record_id}")
    def show(domain: Domain, record_id: Identifier) -> Response:
        return _result(service.show(domain, record_id))

    @app.put("/v1/deliberations/{record_id}")
    async def refuse_deliberation_write(record_id: Identifier, request: Request) -> Response:
        # Historical reasoning records are read-only (SPEC-2098 v2). Read the body before refusing so the refusal
        # itself is delivered: a method-mismatch 405 that leaves the request body unread can surface to the client
        # as a connection reset on Windows.
        await request.body()
        raise PostgresKernelError(
            "read_only_domain",
            "Deliberations are historical reasoning records; the native service offers no write route",
            details={"domain": "deliberations", "id": record_id},
        )

    @app.put("/v1/harnesses/{record_id}")
    def amend_harness(record_id: Identifier, request: HarnessMutation) -> Response:
        return _result(service.amend_harness(record_id, request, project_root=project_root))

    @app.put("/v1/specifications/{record_id}")
    def amend_specification(record_id: Identifier, request: SpecMutation) -> Response:
        return _result(service.amend_specification(record_id, request, project_root=project_root))

    @app.put("/v1/terms/{record_id}")
    def amend_term(record_id: Identifier, request: TermMutation) -> Response:
        return _result(service.amend_term(record_id, request, project_root=project_root))

    @app.put("/v1/tests/{record_id}")
    def amend_test(record_id: Identifier, request: TestMutation) -> Response:
        return _result(service.amend_test(record_id, request, project_root=project_root))

    @app.put("/v1/test-plans/{record_id}")
    def amend_test_plan(record_id: Identifier, request: TestPlanMutation) -> Response:
        return _result(service.amend_test_plan(record_id, request, project_root=project_root))

    @app.put("/v1/test-phases/{record_id}")
    def amend_test_phase(record_id: Identifier, request: TestPhaseMutation) -> Response:
        return _result(service.amend_test_phase(record_id, request, project_root=project_root))

    @app.put("/v1/projects/{record_id}")
    def amend_project(record_id: Identifier, request: ProjectMutation) -> Response:
        return _result(service.amend_project(record_id, request, project_root=project_root))

    @app.post("/v1/projects/{record_id}/planning/revise")
    def revise_program(record_id: Identifier, request: ProgramRevision) -> Response:
        return _result(service.revise_program(record_id, request, project_root=project_root))

    @app.get("/v1/projects/{record_id}/planning/forecast")
    def program_forecast(
        record_id: Identifier, request: Request, as_of: str, native_context_id: str | None = None
    ) -> Response:
        _query_fields(request, {"as_of", "native_context_id"})
        return _result(
            service.program_forecast(
                record_id, as_of=as_of, native_context_id=native_context_id, project_root=project_root
            )
        )

    @app.get("/v1/projects/{record_id}/planning/checkpoint")
    def program_checkpoint(
        record_id: Identifier, request: Request, as_of: str, native_context_id: str | None = None
    ) -> Response:
        _query_fields(request, {"as_of", "native_context_id"})
        return _result(
            service.program_checkpoint(
                record_id, as_of=as_of, native_context_id=native_context_id, project_root=project_root
            )
        )

    @app.get("/v1/projects/{record_id}/planning/change")
    def program_forecast_change(
        record_id: Identifier,
        request: Request,
        from_version: Annotated[int | None, Query(ge=1)] = None,
        to_version: Annotated[int | None, Query(ge=1)] = None,
        native_context_id: str | None = None,
    ) -> Response:
        _query_fields(request, {"from_version", "to_version", "native_context_id"})
        return _result(
            service.program_forecast_change(
                record_id,
                from_version=from_version,
                to_version=to_version,
                native_context_id=native_context_id,
                project_root=project_root,
            )
        )

    @app.put("/v1/projects/{record_id}/authorization")
    def set_project_authorization(record_id: Identifier, request: ProjectAuthorizationChange) -> Response:
        return _result(service.set_project_authorization(record_id, request, project_root=project_root))

    @app.post("/v1/projects/{record_id}/retire")
    def retire_project(record_id: Identifier, request: ProjectRetirement) -> Response:
        return _result(service.retire_project(record_id, request, project_root=project_root))

    @app.put("/v1/project-dependencies/{record_id}")
    def amend_dependency(record_id: Identifier, request: DependencyMutation) -> Response:
        return _result(service.amend_dependency(record_id, request, project_root=project_root))

    @app.put("/v1/project-formal-links/{record_id}")
    def amend_project_formal_link(record_id: FormalLinkIdentifier, request: ProjectFormalLinkMutation) -> Response:
        return _result(service.amend_project_formal_link(record_id, request, project_root=project_root))

    @app.put("/v1/project-memberships/{record_id}")
    def amend_membership_scope(record_id: Identifier, request: MembershipScopeMutation) -> Response:
        return _result(service.amend_membership_scope(record_id, request, project_root=project_root))

    @app.get("/v1/projects/{record_id}/readiness")
    def project_readiness(record_id: Identifier, gate: Literal["readiness", "closure"] = "readiness") -> Response:
        return _result(service.project_readiness(record_id, gate))

    @app.put("/v1/work-items/{record_id}")
    def amend_work_item(record_id: Identifier, request: WorkItemMutation) -> Response:
        return _result(service.amend_work_item(record_id, request, project_root=project_root))

    @app.post("/v1/work-items/{record_id}/move")
    def move(record_id: Identifier, request: MembershipMove) -> Response:
        return _result(service.move_work_item(record_id, request, project_root=project_root))

    @app.post("/v1/work-items/{record_id}/retire")
    def retire_work_item(record_id: Identifier, request: WorkItemRetirement) -> Response:
        return _result(service.retire_work_item(record_id, request, project_root=project_root))
    return app
