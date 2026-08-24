"""Health probes.

/healthz (liveness): the process is up and serving HTTP. Never touches
dependencies — a Postgres outage must not make an orchestrator restart the API.

/readyz (readiness): the instance can do useful work right now. Checks Postgres
and Redis with a hard timeout, returns 503 with per-dependency detail if not.
"""

import asyncio
from collections.abc import Awaitable
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from rag_platform.api.deps import get_engine, get_redis

router = APIRouter(tags=["health"])
log = structlog.get_logger(__name__)

# A hung dependency must read as "not ready", not hang the probe itself —
# k8s-style probes have their own timeouts and a slow 200 is as bad as a 503.
_CHECK_TIMEOUT_S = 2.0


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _check_postgres(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis(redis: Redis) -> None:
    await redis.ping()


@router.get("/readyz")
async def readyz(
    response: Response,
    engine: Annotated[AsyncEngine, Depends(get_engine)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> dict[str, object]:
    checks: dict[str, str] = {}
    probes: list[tuple[str, Awaitable[None]]] = [
        ("postgres", _check_postgres(engine)),
        ("redis", _check_redis(redis)),
    ]
    for name, probe in probes:
        try:
            await asyncio.wait_for(probe, timeout=_CHECK_TIMEOUT_S)
            checks[name] = "ok"
        except Exception as exc:  # noqa: BLE001 — any failure means "not ready"
            checks[name] = "unavailable"
            log.warning("readiness_check_failed", dependency=name, error=str(exc))

    ready = all(v == "ok" for v in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "checks": checks}
