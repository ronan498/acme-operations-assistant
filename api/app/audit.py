"""Audit writer — every authorization decision becomes a row, allow AND deny."""

import json
from typing import Any

import asyncpg

from .auth import Principal
from .config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def record(
    principal: Principal,
    tool: str,
    args: dict[str, Any],
    decision: str,
    reason: str,
    latency_ms: float | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO audit_log
            (actor_sub, actor_name, actor_roles, tool, args, decision, reason,
             latency_ms, request_id, trace_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        principal.sub,
        principal.username,
        sorted(principal.roles),
        tool,
        json.dumps(args),
        decision,
        reason,
        latency_ms,
        request_id,
        trace_id,
    )
