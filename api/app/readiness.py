"""Readiness checks for every downstream dependency.

Each check answers one question: can the api actually use this dependency
right now? All four run concurrently; /ready aggregates them.
"""

import asyncio
import time

import asyncpg
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from redis.asyncio import Redis

from .config import settings

CHECK_TIMEOUT_S = 5.0


async def _check_postgres() -> None:
    conn = await asyncpg.connect(settings.database_url, timeout=3)
    try:
        await conn.fetchval("SELECT 1")
    finally:
        await conn.close()


async def _check_redis() -> None:
    r = Redis.from_url(settings.redis_url)
    try:
        await r.ping()
    finally:
        await r.aclose()


async def _check_keycloak() -> None:
    base = f"{settings.keycloak_url}/realms/{settings.keycloak_realm}"
    url = f"{base}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=3) as client:
        resp = await client.get(url)
        resp.raise_for_status()


async def _check_mcp() -> None:
    # A genuine MCP session over streamable HTTP, not a bare TCP ping:
    # initialize the protocol and list tools.
    async with streamablehttp_client(settings.mcp_server_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.list_tools()


CHECKS = {
    "postgres": _check_postgres,
    "redis": _check_redis,
    "keycloak": _check_keycloak,
    "mcp_server": _check_mcp,
}


async def _run_one(name: str) -> dict:
    start = time.perf_counter()
    try:
        await asyncio.wait_for(CHECKS[name](), timeout=CHECK_TIMEOUT_S)
        status, detail = "up", None
    except Exception as exc:  # noqa: BLE001 — readiness must never raise
        status, detail = "down", f"{type(exc).__name__}: {exc}"
    return {
        "name": name,
        "status": status,
        "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        **({"detail": detail} if detail else {}),
    }


async def run_all() -> tuple[bool, list[dict]]:
    results = await asyncio.gather(*(_run_one(name) for name in CHECKS))
    return all(r["status"] == "up" for r in results), list(results)
