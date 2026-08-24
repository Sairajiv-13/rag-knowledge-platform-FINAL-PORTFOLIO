"""Auth primitives: credential generation, secret hashing, JWT issue/verify.

Design (ADR 0004): OAuth2 client-credentials — each tenant holds machine
credentials, exchanges them at /v1/auth/token for a short-lived HS256 JWT,
and presents that on every call. HS256 over RS256 because there is exactly
one issuer and one verifier (this service); asymmetric keys buy nothing here.
"""

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt

from rag_platform.exceptions import AuthenticationError

_ISSUER = "rag-knowledge-platform"
_ALGORITHM = "HS256"

# Prefixes make leaked credentials grep-able/scannable (the reason GitHub-style
# token prefixes exist) and self-describing in logs and support tickets.
CLIENT_ID_PREFIX = "rag_ci_"
CLIENT_SECRET_PREFIX = "rag_cs_"


def generate_client_id() -> str:
    return CLIENT_ID_PREFIX + secrets.token_hex(12)


def generate_client_secret() -> str:
    return CLIENT_SECRET_PREFIX + secrets.token_urlsafe(32)  # ~256 bits of entropy


def hash_secret(secret: str) -> str:
    # Plain SHA-256, deliberately not bcrypt/argon2: those exist to slow down
    # brute force of LOW-entropy human passwords. A 256-bit random secret is
    # not brute-forceable from its hash, and a fast hash keeps token issuance cheap.
    return hashlib.sha256(secret.encode()).hexdigest()


def secret_matches(secret: str, stored_hash: str) -> bool:
    return secrets.compare_digest(hash_secret(secret), stored_hash)


@dataclass(frozen=True)
class TokenClaims:
    tenant_id: uuid.UUID
    credential_id: uuid.UUID


def create_access_token(
    *, tenant_id: uuid.UUID, credential_id: uuid.UUID, jwt_secret: str, ttl_seconds: int
) -> tuple[str, int]:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": _ISSUER,
        "sub": str(credential_id),
        "tid": str(tenant_id),
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, jwt_secret, algorithm=_ALGORITHM), ttl_seconds


def decode_access_token(token: str, *, jwt_secret: str) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            jwt_secret,
            algorithms=[_ALGORITHM],  # explicit allow-list: never trust the header's alg
            issuer=_ISSUER,
            options={"require": ["exp", "iss", "sub", "tid"]},
        )
        return TokenClaims(
            tenant_id=uuid.UUID(payload["tid"]),
            credential_id=uuid.UUID(payload["sub"]),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        # One generic message on purpose: distinguishing "expired" from
        # "bad signature" from "malformed" helps an attacker more than a user.
        raise AuthenticationError("invalid or expired token") from exc
