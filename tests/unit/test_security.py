"""Credential hashing + JWT issue/verify, including the failure modes."""

import uuid

import pytest

from rag_platform import security
from rag_platform.exceptions import AuthenticationError


def test_generated_credentials_have_prefixes_and_entropy():
    a, b = security.generate_client_id(), security.generate_client_id()
    s = security.generate_client_secret()
    assert a.startswith("rag_ci_") and s.startswith("rag_cs_")
    assert a != b
    assert len(s) > 40


def test_secret_hash_roundtrip():
    secret = security.generate_client_secret()
    stored = security.hash_secret(secret)
    assert security.secret_matches(secret, stored)
    assert not security.secret_matches(secret + "x", stored)
    assert len(stored) == 64  # sha256 hex


K1 = "unit-test-signing-key-0123456789abcdef"
K2 = "different-signing-key-0123456789abcdef"


def _ids():
    return uuid.uuid4(), uuid.uuid4()


def test_jwt_roundtrip_claims():
    tenant_id, credential_id = _ids()
    token, ttl = security.create_access_token(
        tenant_id=tenant_id, credential_id=credential_id, jwt_secret=K1, ttl_seconds=60
    )
    assert ttl == 60
    claims = security.decode_access_token(token, jwt_secret=K1)
    assert claims.tenant_id == tenant_id and claims.credential_id == credential_id


def test_jwt_expired_rejected():
    tenant_id, credential_id = _ids()
    token, _ = security.create_access_token(
        tenant_id=tenant_id, credential_id=credential_id, jwt_secret=K1, ttl_seconds=-10
    )
    with pytest.raises(AuthenticationError):
        security.decode_access_token(token, jwt_secret=K1)


def test_jwt_wrong_key_and_tampering_rejected():
    tenant_id, credential_id = _ids()
    token, _ = security.create_access_token(
        tenant_id=tenant_id, credential_id=credential_id, jwt_secret=K1, ttl_seconds=60
    )
    with pytest.raises(AuthenticationError):
        security.decode_access_token(token, jwt_secret=K2)
    header, payload, sig = token.split(".")
    tampered = f"{header}.{payload[:-2]}AA.{sig}"
    with pytest.raises(AuthenticationError):
        security.decode_access_token(tampered, jwt_secret=K1)
