"""Redis fixed-window rate limiter.

Fixed window (INCR + EXPIRE) over sliding-window/token-bucket on purpose: two
Redis ops, trivially explainable, and the known weakness — a client can burst
up to 2x the limit straddling a window boundary — is an accepted cost at these
limits, documented here rather than hidden.

Fails OPEN: if Redis is down, requests pass with a warning. A rate limiter
exists to protect capacity, not to be a second point of failure for the whole
API; the trade-off is that a Redis outage temporarily removes quota
enforcement.
"""

import time

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from rag_platform.exceptions import RateLimitedError

log = structlog.get_logger(__name__)

_WINDOW_SECONDS = 60


class RateLimiter:
    def __init__(self, redis: Redis, *, limit_per_minute: int) -> None:
        self._redis = redis
        self._limit = limit_per_minute

    async def check(self, key: str) -> None:
        """Raises RateLimitedError (429 + Retry-After) when over the limit."""
        now = int(time.time())
        window = now // _WINDOW_SECONDS
        bucket = f"ratelimit:{key}:{window}"
        try:
            count = await self._redis.incr(bucket)
            if count == 1:
                # +5s slack so a bucket can never linger unexpired forever
                await self._redis.expire(bucket, _WINDOW_SECONDS + 5)
        except RedisError as exc:
            log.warning("rate_limiter_unavailable_failing_open", error=str(exc))
            return
        if count > self._limit:
            retry_after = _WINDOW_SECONDS - (now % _WINDOW_SECONDS)
            raise RateLimitedError(retry_after_seconds=max(retry_after, 1))
