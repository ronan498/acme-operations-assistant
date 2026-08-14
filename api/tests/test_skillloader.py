from app.escalation import OUTPUT_SCHEMA, EscalationSummary
from app.skillloader import load_skill


def test_frontmatter_loads() -> None:
    s = load_skill("customer_escalation_summary")
    assert s.name == "customer_escalation_summary"
    assert s.version == "1.0.0"
    assert "get_customer_profile" in s.allowed_tools
    assert "risk" in s.body.lower()             # tier-2 body present
    assert "When to use:" in s.tool_description  # tier-1 rendering


def test_output_schema_is_strict_compatible() -> None:
    """strict json_schema demands additionalProperties:false and full required."""
    assert OUTPUT_SCHEMA["additionalProperties"] is False
    assert sorted(OUTPUT_SCHEMA["required"]) == sorted(OUTPUT_SCHEMA["properties"])
    # pydantic model and wire schema agree on fields
    assert sorted(EscalationSummary.model_fields) == sorted(OUTPUT_SCHEMA["properties"])
