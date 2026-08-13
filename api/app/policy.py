"""The tool → allowed-roles policy table. One readable file, by design.

Fail-closed: a tool that is missing from this table requires admin. A new
tool someone forgets to annotate cannot silently expose data — it can only
be over-restricted, never under-restricted.

RBAC is enforced HERE, at dispatch, not in the system prompt. No prompt
wording reaches this function.
"""

from dataclasses import dataclass

from .auth import Principal

READ = frozenset({"sales_user", "support_user", "admin"})
SUPPORT_WRITE = frozenset({"support_user", "admin"})
ADMIN_ONLY = frozenset({"admin"})

TOOL_POLICY: dict[str, frozenset[str]] = {
    "get_customer_profile": READ,
    "get_open_issues": READ,
    "summarise_issue_history": READ,
    "add_issue_update": SUPPORT_WRITE,
    "create_next_action": ADMIN_ONLY,
    "update_next_action": ADMIN_ONLY,
    # the Skill invokes read tools; its persist stage re-checks admin itself
    "customer_escalation_summary": READ,
}

FAIL_CLOSED = ADMIN_ONLY


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str


def authorize(principal: Principal, tool: str) -> Decision:
    allowed = TOOL_POLICY.get(tool)
    if allowed is None:
        allowed, prefix = FAIL_CLOSED, "fail_closed_unregistered_tool"
    else:
        prefix = "policy"

    granted = allowed & principal.roles
    if granted:
        return Decision(True, f"{prefix}:role_{sorted(granted)[0]}")
    return Decision(
        False,
        f"{prefix}:insufficient_role (requires one of {sorted(allowed)}, has {sorted(principal.roles)})",
    )
