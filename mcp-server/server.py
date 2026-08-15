"""Acme MCP tool server.

Tool definitions live HERE, never in the agent: the api discovers them at
runtime via list_tools (the §4.2 separation). The server owns all SQL -
nothing else in the system opens a connection to business tables.

Every payload carries "_sql": the exact statements executed (parameters
shown as $n placeholders; values travel separately and are visible in the
tool args). The registry strips "_sql" before the model sees the payload -
it exists for the UI and the audit story, not for the model.

AuthZ note: this server trusts the api (it is unreachable from the host -
no published ports). Role enforcement happens in the api's tool registry
before any call arrives here; the audit trail is written there too.
"""

import os
from datetime import UTC, datetime
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


def _compact(sql: str) -> str:
    return " ".join(sql.split())


def _parse_uuid(value: str) -> str | None:
    import uuid

    try:
        return str(uuid.UUID(value.strip()))
    except (ValueError, AttributeError):
        return None


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# ── SQL: the single source for both execution and the "_sql" report ──

SQL_RESOLVE_CUSTOMER = """
    SELECT id, name FROM customers
    WHERE lower(name) LIKE '%' || lower($1) || '%' ORDER BY name
"""

SQL_CUSTOMER_PROFILE = """
    SELECT c.id, c.name, c.tier, c.industry, c.account_owner, c.created_at,
           count(DISTINCT i.id) FILTER (WHERE i.status != 'resolved') AS open_issues,
           max(GREATEST(i.opened_at, coalesce(u.created_at, i.opened_at))) AS last_activity
    FROM customers c
    LEFT JOIN issues i ON i.customer_id = c.id
    LEFT JOIN issue_updates u ON u.issue_id = i.id
    WHERE lower(c.name) LIKE '%' || lower($1) || '%'
    GROUP BY c.id
    ORDER BY c.name
"""

SQL_OPEN_ISSUES = """
    SELECT i.id, i.title, i.status, i.priority, i.assigned_to, i.opened_at,
           count(u.id) AS update_count, max(u.created_at) AS last_update_at
    FROM issues i
    LEFT JOIN issue_updates u ON u.issue_id = i.id
    WHERE i.customer_id = $1 AND i.status != 'resolved'
    GROUP BY i.id
    ORDER BY array_position(ARRAY['critical','high','medium','low'], i.priority), i.opened_at
"""

SQL_ISSUE = """
    SELECT i.*, c.name AS customer_name FROM issues i
    JOIN customers c ON c.id = i.customer_id WHERE i.id = $1
"""

SQL_ISSUE_UPDATES = """
    SELECT author, body, created_at FROM issue_updates WHERE issue_id = $1 ORDER BY created_at
"""

SQL_ISSUE_ACTIONS = """
    SELECT id, action, status, created_by, created_at, updated_by, updated_at
    FROM next_actions WHERE issue_id = $1 ORDER BY created_at
"""

SQL_INSERT_UPDATE = """
    INSERT INTO issue_updates (id, issue_id, author, body, created_at)
    VALUES (gen_random_uuid(), $1, $2, $3, now())
    RETURNING id, created_at
"""

SQL_INSERT_ACTION = """
    INSERT INTO next_actions (id, issue_id, action, status, created_by)
    VALUES (gen_random_uuid(), $1, $2, 'proposed', $3)
    RETURNING id, created_at
"""

SQL_UPDATE_ACTION = """
    UPDATE next_actions
    SET status = coalesce($2, status),
        action = coalesce($3, action),
        updated_by = $4,
        updated_at = now()
    WHERE id = $1
    RETURNING id, action, status, updated_at
"""


async def _resolve_customer(customer_name: str) -> tuple[dict[str, Any] | None, Any]:
    """Resolve a customer name to a single row, or an ambiguity/not-found payload."""
    p = await pool()
    rows = await p.fetch(SQL_RESOLVE_CUSTOMER, customer_name.strip())
    exact = [r for r in rows if r["name"].lower() == customer_name.strip().lower()]
    if len(exact) == 1:
        rows = exact
    if not rows:
        return {
            "_sql": [_compact(SQL_RESOLVE_CUSTOMER)],
            "found": False,
            "message": f"No customer matching '{customer_name}'.",
        }, None
    if len(rows) > 1:
        return {
            "_sql": [_compact(SQL_RESOLVE_CUSTOMER)],
            "found": False,
            "ambiguous": True,
            "candidates": [r["name"] for r in rows],
            "message": "Multiple customers match - ask the user which one they mean.",
        }, None
    return None, rows[0]


@mcp.tool()
async def get_customer_profile(customer_name: str) -> dict[str, Any]:
    """Retrieve a customer's profile by name: tier, industry, account owner,
    open-issue count and latest activity. If the name matches more than one
    customer, returns the candidate list instead - ask the user which one
    they mean before proceeding."""
    p = await pool()
    rows = await p.fetch(SQL_CUSTOMER_PROFILE, customer_name.strip())

    # exact (case-insensitive) match wins outright even when a substring
    # search would be ambiguous - "Northwind Logistics" must not trip over
    # "Northwind Retail Co."
    exact = [r for r in rows if r["name"].lower() == customer_name.strip().lower()]
    if len(exact) == 1:
        rows = exact

    sql = [_compact(SQL_CUSTOMER_PROFILE)]
    if not rows:
        return {"_sql": sql, "found": False, "message": f"No customer matching '{customer_name}'."}
    if len(rows) > 1:
        return {
            "_sql": sql,
            "found": False,
            "ambiguous": True,
            "candidates": [r["name"] for r in rows],
            "message": "Multiple customers match - ask the user which one they mean.",
        }

    r = rows[0]
    return {
        "_sql": sql,
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


@mcp.tool()
async def get_open_issues(customer_name: str) -> dict[str, Any]:
    """List all unresolved issues for a customer: id, title, status, priority,
    assignee, age in days, and update activity. Use the returned issue_id
    values with summarise_issue_history or the write tools."""
    err, customer = await _resolve_customer(customer_name)
    if err:
        return err
    p = await pool()
    rows = await p.fetch(SQL_OPEN_ISSUES, customer["id"])
    return {
        "_sql": [_compact(SQL_RESOLVE_CUSTOMER), _compact(SQL_OPEN_ISSUES)],
        "found": True,
        "customer": customer["name"],
        "open_issue_count": len(rows),
        "issues": [
            {
                "issue_id": str(r["id"]),
                "title": r["title"],
                "status": r["status"],
                "priority": r["priority"],
                "assigned_to": r["assigned_to"],  # null means unassigned
                "opened_at": r["opened_at"].date().isoformat(),
                "age_days": (datetime.now(UTC) - r["opened_at"]).days,
                "update_count": r["update_count"],
                "last_update_at": r["last_update_at"].date().isoformat() if r["last_update_at"] else None,
            }
            for r in rows
        ],
    }


@mcp.tool()
async def summarise_issue_history(issue_id: str) -> dict[str, Any]:
    """Full history of one issue: the issue record, every update in
    chronological order, and any next actions attached to it. The caller
    summarises; this tool returns the verbatim facts."""
    iid = _parse_uuid(issue_id)
    if iid is None:
        return {"found": False, "message": f"'{issue_id}' is not a valid issue id."}
    p = await pool()
    issue = await p.fetchrow(SQL_ISSUE, iid)
    if issue is None:
        return {"_sql": [_compact(SQL_ISSUE)], "found": False, "message": f"No issue with id {issue_id}."}
    updates = await p.fetch(SQL_ISSUE_UPDATES, iid)
    actions = await p.fetch(SQL_ISSUE_ACTIONS, iid)
    return {
        "_sql": [_compact(SQL_ISSUE), _compact(SQL_ISSUE_UPDATES), _compact(SQL_ISSUE_ACTIONS)],
        "found": True,
        "issue": {
            "issue_id": str(issue["id"]),
            "customer": issue["customer_name"],
            "title": issue["title"],
            "description": issue["description"],
            "status": issue["status"],
            "priority": issue["priority"],
            "assigned_to": issue["assigned_to"],
            "opened_at": issue["opened_at"].date().isoformat(),
        },
        "updates": [
            {"author": u["author"], "body": u["body"], "at": u["created_at"].date().isoformat()}
            for u in updates
        ],
        "next_actions": [
            {
                "action_id": str(a["id"]),
                "action": a["action"],
                "status": a["status"],
                "created_by": a["created_by"],
            }
            for a in actions
        ],
    }


@mcp.tool()
async def add_issue_update(issue_id: str, body: str, actor: str) -> dict[str, Any]:
    """Append an update to an issue's history. The actor field is set by the
    platform, never by the model."""
    iid = _parse_uuid(issue_id)
    if iid is None:
        return {"ok": False, "message": f"'{issue_id}' is not a valid issue id."}
    if not body.strip():
        return {"ok": False, "message": "Update body must not be empty."}
    p = await pool()
    row = await p.fetchrow(SQL_INSERT_UPDATE, iid, actor, body.strip())
    if row is None:
        return {"ok": False, "message": f"No issue with id {issue_id}."}
    return {
        "_sql": [_compact(SQL_INSERT_UPDATE)],
        "ok": True,
        "update_id": str(row["id"]),
        "author": actor,
        "at": row["created_at"].isoformat(),
    }


@mcp.tool()
async def create_next_action(issue_id: str, action: str, actor: str) -> dict[str, Any]:
    """Create a recommended next action for an issue. The actor field is set
    by the platform, never by the model."""
    iid = _parse_uuid(issue_id)
    if iid is None:
        return {"ok": False, "message": f"'{issue_id}' is not a valid issue id."}
    if not action.strip():
        return {"ok": False, "message": "Action text must not be empty."}
    p = await pool()
    row = await p.fetchrow(SQL_INSERT_ACTION, iid, action.strip(), actor)
    return {
        "_sql": [_compact(SQL_INSERT_ACTION)],
        "ok": True,
        "action_id": str(row["id"]),
        "status": "proposed",
        "created_by": actor,
        "at": row["created_at"].isoformat(),
    }


@mcp.tool()
async def update_next_action(
    action_id: str, actor: str, status: str | None = None, action_text: str | None = None
) -> dict[str, Any]:
    """Update an existing next action's status (proposed/in_progress/done/
    cancelled) and/or its text. The actor field is set by the platform,
    never by the model."""
    aid = _parse_uuid(action_id)
    if aid is None:
        return {"ok": False, "message": f"'{action_id}' is not a valid action id."}
    allowed = {"proposed", "in_progress", "done", "cancelled"}
    if status is not None and status not in allowed:
        return {"ok": False, "message": f"status must be one of {sorted(allowed)}."}
    if status is None and action_text is None:
        return {"ok": False, "message": "Nothing to update: provide status and/or action_text."}
    p = await pool()
    row = await p.fetchrow(SQL_UPDATE_ACTION, aid, status, action_text, actor)
    if row is None:
        return {"_sql": [_compact(SQL_UPDATE_ACTION)], "ok": False, "message": f"No next action with id {action_id}."}
    return {
        "_sql": [_compact(SQL_UPDATE_ACTION)],
        "ok": True,
        "action_id": str(row["id"]),
        "action": row["action"],
        "status": row["status"],
        "updated_by": actor,
        "at": row["updated_at"].isoformat(),
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
