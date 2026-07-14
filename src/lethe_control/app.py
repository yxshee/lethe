from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from lethe_control.auth import require_control_token, require_unsafe_token, resolve_principal
from lethe_control.config import Settings
from lethe_control.errors import LetheError
from lethe_control.models import (
    EventAccepted,
    ExecutionReceipt,
    GraphResponse,
    HealthResponse,
    LifecycleEvent,
    PostureAssessmentReport,
    QueryRequest,
    QueryResponse,
    ReceiptVerifyRequest,
    ReceiptVerifyResponse,
    RunStatusResponse,
    ScanRequest,
    ScanResponse,
)
from lethe_control.service import LetheService


def create_app(
    settings: Settings | None = None,
    service: LetheService | None = None,
    *,
    start_worker: bool = True,
) -> FastAPI:
    configured = settings or Settings.from_env()
    lifecycle_service = service or LetheService(configured)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        lifecycle_service.initialize(seed_if_empty=True)
        if start_worker:
            lifecycle_service.start_worker()
        yield
        lifecycle_service.stop_worker()

    app = FastAPI(
        title="Lethe v0.1 — internal codename",
        version="0.1.0",
        description=(
            "Closed-world localhost proof. Receipts are scoped signer assertions, "
            "not universal deletion proof."
        ),
        lifespan=lifespan,
    )
    app.state.lethe = lifecycle_service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Lethe-Event-Signature"],
    )

    @app.exception_handler(LetheError)
    async def handle_lethe_error(_: Request, error: LetheError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"message": error.message, "reason_code": error.code},
        )

    @app.get("/healthz", response_model=HealthResponse)
    def health() -> HealthResponse:
        return lifecycle_service.health()

    @app.get("/readyz", response_model=HealthResponse)
    def ready() -> HealthResponse:
        return lifecycle_service.health()

    @app.post(
        "/api/v1/events",
        response_model=EventAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def accept_event(
        event: LifecycleEvent,
        authorization: str | None = Header(default=None),
        event_signature: str | None = Header(default=None, alias="X-Lethe-Event-Signature"),
    ) -> EventAccepted:
        require_control_token(configured, authorization)
        if not event_signature:
            from lethe_control.errors import AuthorizationError

            raise AuthorizationError("missing_event_signature", "event signature required")
        return lifecycle_service.accept_event(event, event_signature)

    @app.get("/api/v1/runs/{run_id}", response_model=RunStatusResponse)
    def get_run(run_id: str, authorization: str | None = Header(default=None)) -> RunStatusResponse:
        require_control_token(configured, authorization)
        return lifecycle_service.run_status(run_id)

    @app.get("/api/v1/graph/{version_id}", response_model=GraphResponse)
    def get_graph(
        version_id: str, authorization: str | None = Header(default=None)
    ) -> GraphResponse:
        require_control_token(configured, authorization)
        return lifecycle_service.graph(lifecycle_service.scope, version_id)

    @app.post("/api/v1/query", response_model=QueryResponse)
    def query(
        body: QueryRequest, authorization: str | None = Header(default=None)
    ) -> QueryResponse:
        principal = resolve_principal(configured, authorization)
        return lifecycle_service.query(body, principal_ref=principal)

    @app.post("/api/v1/demo/unsafe-query", response_model=QueryResponse)
    def unsafe_query(
        body: QueryRequest, authorization: str | None = Header(default=None)
    ) -> QueryResponse:
        require_unsafe_token(configured, authorization)
        return lifecycle_service.unsafe_query(body)

    @app.post("/api/v1/scans", response_model=ScanResponse)
    def scan(body: ScanRequest, authorization: str | None = Header(default=None)) -> ScanResponse:
        require_control_token(configured, authorization)
        return lifecycle_service.scan(body)

    @app.post("/api/v1/assessments", response_model=PostureAssessmentReport)
    def assess(
        body: ScanRequest, authorization: str | None = Header(default=None)
    ) -> PostureAssessmentReport:
        require_control_token(configured, authorization)
        return lifecycle_service.assess(body)

    @app.get("/api/v1/receipts/{run_id}", response_model=ExecutionReceipt)
    def get_receipt(
        run_id: str, authorization: str | None = Header(default=None)
    ) -> ExecutionReceipt:
        require_control_token(configured, authorization)
        return lifecycle_service.get_receipt(run_id)

    @app.post("/api/v1/receipts/verify", response_model=ReceiptVerifyResponse)
    def verify_receipts(
        body: ReceiptVerifyRequest,
        authorization: str | None = Header(default=None),
    ) -> ReceiptVerifyResponse:
        require_control_token(configured, authorization)
        return lifecycle_service.verify_receipts(body)

    return app
