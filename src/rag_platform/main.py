"""Application entrypoint and factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from rag_platform.api.deps import enforce_rate_limit
from rag_platform.api.routes import answers, auth, documents, health, search, usage
from rag_platform.config import Settings, get_settings
from rag_platform.db import create_engine, create_session_factory
from rag_platform.exceptions import RagPlatformError
from rag_platform.llm.factory import build_embedding_provider, build_llm_provider, build_reranker
from rag_platform.logging import configure_logging
from rag_platform.observability.metrics import MetricsMiddleware
from rag_platform.observability.request_id import RequestIdMiddleware
from rag_platform.observability.tracing import configure_tracing

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    # Clients connect lazily on first use: startup must not depend on
    # Postgres/Redis being up (liveness != readiness). /readyz is what
    # gates traffic, not process boot.
    app.state.engine = create_engine(str(settings.database_url))
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.redis = aioredis.from_url(str(settings.redis_url))
    # Providers are process-wide singletons: the local embedder holds a loaded
    # model; building it per-request would be catastrophic. Fail-fast at boot
    # if misconfigured (e.g. anthropic without a key).
    app.state.embedder = build_embedding_provider(settings)
    app.state.reranker = build_reranker(settings)
    app.state.llm = build_llm_provider(settings)
    log.info("app_started", environment=settings.environment)
    yield
    await app.state.engine.dispose()
    await app.state.redis.aclose()
    log.info("app_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Factory (rather than a module-level singleton built inline) so tests
    can construct isolated apps with their own Settings."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    configure_tracing(app, settings)
    # add_middleware order: last-added runs first, so RequestId wraps Metrics —
    # even a request that only produces metrics/logs carries its id.
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router, prefix="/v1")  # own limiter inside (no tenant yet)
    for router in (documents.router, search.router, answers.router, usage.router):
        app.include_router(
            router, prefix="/v1", dependencies=[Depends(enforce_rate_limit)]
        )

    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    # A plain route, not app.mount(): mounting triggers Starlette's trailing-
    # slash redirect (/metrics -> /metrics/), which scrapers don't follow.
    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.exception_handler(RagPlatformError)
    async def handle_app_error(request: Request, exc: RagPlatformError) -> JSONResponse:
        # Expected domain errors -> clean JSON with the subclass's status code.
        # Anything else still hits FastAPI's default 500 + traceback in logs.
        log.warning(
            "application_error",
            error_type=type(exc).__name__,
            detail=exc.detail,
            path=request.url.path,
        )
        headers = dict(exc.headers or {})  # e.g. Retry-After on 429
        if exc.status_code == 401:
            # RFC 6750: a 401 MUST tell the client which auth scheme to use.
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail}, headers=headers or None
        )

    return app


app = create_app()
