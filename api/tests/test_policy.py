from app.auth import Principal
from app.policy import authorize


def p(*roles: str) -> Principal:
    return Principal(sub="00000000-0000-0000-0000-000000000000", username="t", roles=frozenset(roles))


def test_role_ladder() -> None:
    """Every §4.4 tier is distinct — each cell here mirrors the eval set."""
    assert authorize(p("sales_user"), "get_open_issues").allow
    assert not authorize(p("sales_user"), "add_issue_update").allow
    assert not authorize(p("sales_user"), "create_next_action").allow
    assert authorize(p("support_user"), "add_issue_update").allow
    assert not authorize(p("support_user"), "create_next_action").allow
    assert authorize(p("admin"), "create_next_action").allow
    assert authorize(p("admin"), "update_next_action").allow


def test_unregistered_tool_fails_closed() -> None:
    """A tool nobody annotated must require admin, never default open."""
    decision = authorize(p("support_user"), "brand_new_forgotten_tool")
    assert not decision.allow
    assert "fail_closed" in decision.reason
    assert authorize(p("admin"), "brand_new_forgotten_tool").allow


def test_no_roles_denied_everywhere() -> None:
    assert not authorize(p(), "get_customer_profile").allow
