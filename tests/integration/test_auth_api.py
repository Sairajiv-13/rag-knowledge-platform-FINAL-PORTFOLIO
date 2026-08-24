"""Token endpoint + auth enforcement against the real DB."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from rag_platform.models import ApiCredential


async def test_token_issued_and_last_used_recorded(client, auth, engine):
    async with async_sessionmaker(engine)() as session:
        cred = (
            await session.execute(
                select(ApiCredential).where(ApiCredential.client_id == auth["client_id"])
            )
        ).scalar_one()
        assert cred.last_used_at is not None


async def test_wrong_secret_is_401_with_generic_message(client, auth):
    resp = await client.post(
        "/v1/auth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": auth["client_id"],
            "client_secret": "rag_cs_wrong",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid client credentials"  # same as unknown-client path


async def test_unknown_client_gets_identical_error(client):
    resp = await client.post(
        "/v1/auth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "rag_ci_missing",
            "client_secret": "rag_cs_x",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid client credentials"


async def test_unsupported_grant_type_is_400(client, auth):
    resp = await client.post(
        "/v1/auth/token",
        data={
            "grant_type": "password",
            "client_id": auth["client_id"],
            "client_secret": auth["client_secret"],
        },
    )
    assert resp.status_code == 400


async def test_missing_token_is_401_with_www_authenticate(client):
    resp = await client.get("/v1/documents")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


async def test_garbage_token_is_401(client):
    resp = await client.get("/v1/documents", headers={"Authorization": "Bearer nope.nope.nope"})
    assert resp.status_code == 401


async def test_revocation_is_immediate(client, auth, engine):
    async with async_sessionmaker(engine)() as session:
        cred = (
            await session.execute(
                select(ApiCredential).where(ApiCredential.client_id == auth["client_id"])
            )
        ).scalar_one()
        cred.revoked_at = datetime.now(UTC)
        await session.commit()
    resp = await client.get("/v1/documents", headers=auth["headers"])  # token still unexpired
    assert resp.status_code == 401
