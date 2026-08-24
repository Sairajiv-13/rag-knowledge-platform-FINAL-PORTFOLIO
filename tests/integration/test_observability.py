"""Request IDs, metrics exposition, and rate limiting."""

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from rag_platform.config import Settings


async def _redis_available() -> bool:
    try:
        r = Redis.from_url(Settings().redis_url.unicode_string())
        await r.ping()
        await r.aclose()
        return True
    except Exception:
        return False


async def test_request_id_generated_and_echoed(client):
    resp = await client.get("/healthz")
    rid = resp.headers["x-request-id"]
    uuid.UUID(rid)  # a real uuid, not a constant


async def test_incoming_request_id_is_honored(client):
    resp = await client.get("/healthz", headers={"X-Request-ID": "upstream-abc-123"})
    assert resp.headers["x-request-id"] == "upstream-abc-123"


async def test_metrics_exposition_uses_route_templates(client, auth):
    doc = await client.post(
        "/v1/documents", headers=auth["headers"],
        files={"file": ("m.md", b"# M\n\nMetrics body.\n")},
    )
    await client.get(f"/v1/documents/{doc.json()['id']}", headers=auth["headers"])
    body = (await client.get("/metrics")).text
    assert "http_requests_total" in body and "http_request_duration_seconds_bucket" in body
    # template label, not the raw uuid path (cardinality protection)
    assert '/v1/documents/{document_id}' in body
    assert doc.json()["id"] not in body


async def test_llm_token_counter_increments_on_answers(client, auth):
    await client.post(
        "/v1/documents", headers=auth["headers"],
        files={"file": ("t.md", b"# T\n\nToken counting body.\n")},
    )
    await client.post("/v1/answers", headers=auth["headers"], json={"query": "token counting?"})
    body = (await client.get("/metrics")).text
    assert 'rag_llm_tokens_total{direction="input",model="fake-llm"}' in body


async def test_data_plane_rate_limit_returns_429_with_retry_after(make_auth):
    if not await _redis_available():
        pytest.skip("redis not reachable; limiter fails open by design")
    from rag_platform.main import create_app

    app = create_app(Settings(rate_limit_per_minute=2))
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            # fresh tenant -> fresh limit bucket
            auth = await _auth_on(client, make_auth)
            assert (await client.get("/v1/documents", headers=auth)).status_code == 200
            assert (await client.get("/v1/documents", headers=auth)).status_code == 200
            resp = await client.get("/v1/documents", headers=auth)
            assert resp.status_code == 429
            assert 1 <= int(resp.headers["retry-after"]) <= 60
            # health and metrics are never rate limited
            assert (await client.get("/healthz")).status_code == 200


async def test_token_endpoint_has_its_own_tighter_limit(make_auth):
    if not await _redis_available():
        pytest.skip("redis not reachable; limiter fails open by design")
    from rag_platform.main import create_app

    app = create_app(Settings(rate_limit_token_per_minute=2))
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            data = {
                "grant_type": "client_credentials",
                "client_id": f"rag_ci_{uuid.uuid4().hex[:12]}",  # unknown id: still throttled
                "client_secret": "rag_cs_wrong",
            }
            assert (await client.post("/v1/auth/token", data=data)).status_code == 401
            assert (await client.post("/v1/auth/token", data=data)).status_code == 401
            assert (await client.post("/v1/auth/token", data=data)).status_code == 429


async def _auth_on(client, make_auth):
    """make_auth uses the shared client fixture app; re-issue a token against
    this test's own app instance (same DB, same JWT secret)."""
    base = await make_auth(f"rl-{uuid.uuid4().hex[:8]}")
    resp = await client.post(
        "/v1/auth/token",
        data={"grant_type": "client_credentials", "client_id": base["client_id"],
              "client_secret": base["client_secret"]},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
