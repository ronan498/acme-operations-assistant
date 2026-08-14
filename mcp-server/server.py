"""Acme MCP tool server.

Tool definitions live HERE, never in the agent: the api discovers them at
runtime via list_tools (the §4.2 separation). The server owns all SQL —
nothing else in the system opens a connection to business tables.

AuthZ note: this server trusts the api (it is unreachable from the host —
no published ports). Role enforcement happens in the api's tool registry
before any call arrives here; the audit trail is written there too.
"""

import os
from typing import Any

import asyncpg
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://acme:acme_dev_password@postgres:5432/acme"
)

mcp = FastMCP("acme-tools", host="0.0.0.0", port=8765)

_pool: asyncpg.Pool | None = None


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return _pool


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.tool()
async def get_customer_profile(customer_name: str) -> dict[str, Any]:
    """Retrieve a customer's profile by name: tier, industry, account owner,
    open-issue count and latest activity. If the name matches more than one
    customer, returns the candidate list instead — ask the user which one
    they mean before proceeding."""
    p = await pool()
    rows = await p.fetch(
        """
        SELECT c.id, c.name, c.tier, c.industry, c.account_owner, c.created_at,
               count(i.id) FILTER (WHERE i.status != 'resolved') AS open_issues,
               max(GREATEST(i.opened_at, coalesce(u.created_at, i.opened_at))) AS last_activity
        FROM customers c
        LEFT JOIN issues i ON i.customer_id = c.id
        LEFT JOIN issue_updates u ON u.issue_id = i.id
        WHERE lower(c.name) LIKE '%' || lower($1) || '%'
        GROUP BY c.id
        ORDER BY c.name
        """,
        customer_name.strip(),
    )

    # exact (case-insensitive) match wins outright even when a substring
    # search would be ambiguous — "Northwind Logistics" must not trip over
    # "Northwind Retail Co."
    exact = [r for r in rows if r["name"].lower() == customer_name.strip().lower()]
    if len(exact) == 1:
        rows = exact

    if not rows:
        return {"found": False, "message": f"No customer matching '{customer_name}'."}
    if len(rows) > 1:
        return {
            "found": False,
            "ambiguous": True,
            "candidates": [r["name"] for r in rows],
            "message": "Multiple customers match — ask the user which one they mean.",
        }

    r = rows[0]
    return {
        "found": True,
        "customer_id": str(r["id"]),
        "name": r["name"],
        "tier": r["tier"],
        "industry": r["industry"],
        "account_owner": r["account_owner"],  # null is meaningful: unowned account
        "customer_since": r["created_at"].date().isoformat(),
        "open_issues": r["open_issues"],
        "last_activity": r["last_activity"].date().isoformat() if r["last_activity"] else None,
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
