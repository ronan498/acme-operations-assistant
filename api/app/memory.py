"""Redis wrappers — the two distinct jobs Redis does here (§4.7).

SessionMemory: short-term conversation state. Keys bind the principal's
subject to the session id, so one user can never resume another's
conversation regardless of what session id they present.

LookupCache: read-through cache for tool results with a TTL. Redis is
never the source of truth — anything a write depends on is re-read from
Postgres first (see DECISIONS.md).
"""

import json
from typing import Any

from redis.asyncio import Redis

from .config import settings

MAX_TURNS = 20
SESSION_TTL_S = 60 * 60      # a working session, not durable storage
CACHE_TTL_S = 300


def _client() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


class SessionMemory:
    def __init__(self, redis: Redis | None = None) -> None:
        self._r = redis or _client()

    @staticmethod
    def _key(sub: str, session_id: str) -> str:
        return f"acme:session:{sub}:{session_id}"

    async def append_turn(self, sub: str, session_id: str, turn: dict[str, Any]) -> None:
        key = self._key(sub, session_id)
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.rpush(key, json.dumps(turn))
            pipe.ltrim(key, -MAX_TURNS, -1)
            pipe.expire(key, SESSION_TTL_S)
            await pipe.execute()

    async def get_turns(self, sub: str, session_id: str) -> list[dict[str, Any]]:
        raw = await self._r.lrange(self._key(sub, session_id), 0, -1)
        return [json.loads(item) for item in raw]

    async def aclose(self) -> None:
        await self._r.aclose()


class LookupCache:
    def __init__(self, redis: Redis | None = None) -> None:
        self._r = redis or _client()

    @staticmethod
    def _key(namespace: str, ref: str) -> str:
        return f"acme:cache:{namespace}:{ref.strip().lower()}"

    async def get(self, namespace: str, ref: str) -> Any | None:
        raw = await self._r.get(self._key(namespace, ref))
        return json.loads(raw) if raw is not None else None

    async def set(self, namespace: str, ref: str, value: Any, ttl_s: int = CACHE_TTL_S) -> None:
        await self._r.set(self._key(namespace, ref), json.dumps(value), ex=ttl_s)

    async def aclose(self) -> None:
        await self._r.aclose()
