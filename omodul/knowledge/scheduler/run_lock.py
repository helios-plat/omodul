"""Redis-backed distributed lock to prevent duplicate scheduled job execution."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager


class _LockResult:
    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired


class RunLock:
    """Redis lock that prevents multiple scheduler instances running the same job."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
        self._client = None

    def _get_client(self):
        if self._client is None:
            import redis.asyncio as redis
            self._client = redis.from_url(self._redis_url)
        return self._client

    @asynccontextmanager
    async def acquire(self, job_id: str, ttl_seconds: int = 3600):
        client = self._get_client()
        key = f"stratum:scheduler:lock:{job_id}"
        lock = client.lock(key, timeout=ttl_seconds)
        acquired = await lock.acquire(blocking=False)
        try:
            yield _LockResult(acquired)
        finally:
            if acquired:
                try:
                    await lock.release()
                except Exception:
                    pass

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
