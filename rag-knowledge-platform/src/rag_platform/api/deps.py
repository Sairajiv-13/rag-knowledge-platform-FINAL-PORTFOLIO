"""FastAPI dependencies.

Shared clients live on app.state (created once in the lifespan) and are exposed
to routes through these thin accessors, so tests can swap them via
app.dependency_overrides without monkeypatching module globals.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.openapi.models import OAuthFlowClientCredentials, OAuthFlows
from fastapi.security import OAuth2
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from rag_platform.config import Settings
from rag_platform.exceptions import AuthenticationError
from rag_platform.models import ApiCredential
from rag_platform.ratelimit import RateLimiter
from rag_platform.retrieval.service import RetrievalService
from rag_platform.security import TokenClaims, decode_access_token
from rag_platform.services.answering import AnswerService
from rag_platform.services.ingestion import IngestionService

# auto_error=False: FastAPI's default 403-without-WWW-Authenticate is wrong for
# bearer auth; our own AuthenticationError -> 401 + WWW-Authenticate header.
oauth2_scheme = OAuth2(
    flows=OAuthFlows(clientCredentials=OAuthFlowClientCredentials(tokenUrl="/v1/auth/token")),
    scheme_name="OAuth2ClientCredentials",
    auto_error=False,
)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_engine(request: Request) -> AsyncEngine:
    return request.app.state.engine  # type: ignore[no-any-return]


def get_redis(request: Request) -> Redis:
    return request.app.state.redis  # type: ignore[no-any-return]


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request; commits are explicit in the service layer so a
    handler can never half-commit by accident. Rollback on error is implicit
    in the context manager."""
    async with request.app.state.session_factory() as session:
        yield session


async def get_current_tenant(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[str | None, Depends(oauth2_scheme)],
) -> TokenClaims:
    """JWT check + credential liveness check.

    The DB lookup on every request is a deliberate trade: one PK read buys
    *immediate* revocation instead of "revoked keys keep working for up to
    jwt_ttl". A Redis cache in front is the documented optimization if this
    read ever shows up in profiles (ADR 0004).
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("missing bearer token")
    claims = decode_access_token(
        authorization[7:], jwt_secret=settings.jwt_secret.get_secret_value()
    )
    credential = await session.get(ApiCredential, claims.credential_id)
    if (
        credential is None
        or credential.revoked_at is not None
        or credential.tenant_id != claims.tenant_id
    ):
        raise AuthenticationError("credential revoked or unknown")
    return claims


async def enforce_rate_limit(
    request: Request,
    tenant: Annotated[TokenClaims, Depends(get_current_tenant)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> None:
    """Applied to every data-plane router (see main.py). Keyed by TENANT,
    not credential: issuing more credentials must not multiply a tenant's
    quota. FastAPI caches get_current_tenant per request, so this adds no
    second auth/DB hit."""
    limiter = RateLimiter(
        request.app.state.redis, limit_per_minute=settings.rate_limit_per_minute
    )
    await limiter.check(f"tenant:{tenant.tenant_id}")


CurrentTenant = Annotated[TokenClaims, Depends(get_current_tenant)]
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def get_ingestion_service(request: Request) -> IngestionService:
    settings: Settings = request.app.state.settings
    return IngestionService(
        request.app.state.embedder,
        chunk_target_tokens=settings.chunk_target_tokens,
        chunk_overlap_tokens=settings.chunk_overlap_tokens,
        embed_batch_size=settings.embed_batch_size,
        max_tenant_storage_bytes=settings.max_tenant_storage_bytes,
    )


def get_retrieval_service(request: Request) -> RetrievalService:
    settings: Settings = request.app.state.settings
    return RetrievalService(
        request.app.state.embedder,
        request.app.state.reranker,
        k_dense=settings.retrieval_k_dense,
        k_keyword=settings.retrieval_k_keyword,
        rrf_k=settings.retrieval_rrf_k,
        top_n=settings.retrieval_top_n,
    )


def get_answer_service(request: Request) -> AnswerService:
    settings: Settings = request.app.state.settings
    return AnswerService(
        get_retrieval_service(request),
        request.app.state.llm,
        request.app.state.session_factory,
        max_tokens=settings.answer_max_tokens,
        price_input_per_mtok=settings.price_input_per_mtok,
        price_output_per_mtok=settings.price_output_per_mtok,
    )
