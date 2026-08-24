"""OAuth2 token endpoint (client-credentials grant, RFC 6749 §4.4)."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Form, Request
from sqlalchemy import select

from rag_platform.api.deps import DbSession, SettingsDep
from rag_platform.api.schemas import TokenResponse
from rag_platform.exceptions import AuthenticationError, InvalidGrantError
from rag_platform.models import ApiCredential
from rag_platform.ratelimit import RateLimiter
from rag_platform.security import create_access_token, hash_secret, secret_matches

router = APIRouter(tags=["auth"])

# Compared against when the client_id is unknown, so "no such client" and
# "wrong secret" take the same code path — no timing oracle on client_id.
_DUMMY_HASH = hash_secret("timing-equalizer")


@router.post("/auth/token", response_model=TokenResponse)
async def issue_token(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    client_secret: Annotated[str, Form()],
) -> TokenResponse:
    # Unauthenticated endpoint -> its own (tighter) limit, keyed by client_id,
    # checked before any secret comparison: brute-force gets throttled cheaply.
    limiter = RateLimiter(
        request.app.state.redis, limit_per_minute=settings.rate_limit_token_per_minute
    )
    await limiter.check(f"token:{client_id}")

    if grant_type != "client_credentials":
        raise InvalidGrantError("unsupported grant_type; use client_credentials")

    credential = (
        await session.execute(select(ApiCredential).where(ApiCredential.client_id == client_id))
    ).scalar_one_or_none()

    if credential is None:
        secret_matches(client_secret, _DUMMY_HASH)
        raise AuthenticationError("invalid client credentials")
    if credential.revoked_at is not None or not secret_matches(
        client_secret, credential.secret_hash
    ):
        # Same message for revoked/wrong-secret: don't confirm a live client_id.
        raise AuthenticationError("invalid client credentials")

    credential.last_used_at = datetime.now(UTC)
    await session.commit()

    token, ttl = create_access_token(
        tenant_id=credential.tenant_id,
        credential_id=credential.id,
        jwt_secret=settings.jwt_secret.get_secret_value(),
        ttl_seconds=settings.jwt_ttl_seconds,
    )
    return TokenResponse(access_token=token, expires_in=ttl)
