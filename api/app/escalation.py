"""Customer Escalation Summary Skill — four stages, structurally distinct
from a one-off prompt call (§4.3):

    1. gather   — deterministic tool calls through the registry (no LLM)
    2. reason   — ONE structured-output call, instructions = SKILL.md body
    3. validate — Pydantic schema enforcement, one repair attempt
    4. persist  — optional next_action write, gated by the SAME registry
                  dispatch as any other write: the Skill has no privilege
                  its caller lacks

Sub-tool calls run as the calling principal, so every gather step is
individually authorized and audited.
"""

import asyncio
import json
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .auth import Principal
from .llm import llm
from .skillloader import load_skill

MAX_HISTORIES = 5
SKILL = load_skill("customer_escalation_summary")


class EscalationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executive_summary: str
    risk_level: Literal["Low", "Medium", "High", "Critical"]
    risk_rationale: str
    recommended_next_action: str
    missing_information: list[str]


OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "executive_summary",
        "risk_level",
        "risk_rationale",
        "recommended_next_action",
        "missing_information",
    ],
    "properties": {
        "executive_summary": {"type": "string"},
        "risk_level": {"type": "string", "enum": ["Low", "Medium", "High", "Critical"]},
        "risk_rationale": {"type": "string"},
        "recommended_next_action": {"type": "string"},
        "missing_information": {"type": "array", "items": {"type": "string"}},
    },
}


async def run_skill(principal: Principal, args: dict[str, Any]) -> tuple[str, bool]:
    """Returns (content_json, is_error) for the outer agent to present."""
    from .registry import registry  # function-level import breaks the module cycle

    async def dispatch(tool: str, payload: dict[str, Any]):
        # the frontmatter's allowed_tools is a contract, enforced not implied
        if tool not in SKILL.allowed_tools:
            raise ValueError(f"skill tried undeclared tool {tool!r}")
        return await registry.dispatch(principal, tool, json.dumps(payload))

    customer_name = str(args.get("customer_name", "")).strip()
    persist = bool(args.get("persist_next_action", False))
    timings: dict[str, float] = {}

    # ── stage 1: gather (deterministic, no LLM) ──
    t0 = time.perf_counter()
    profile_out = await dispatch("get_customer_profile", {"customer_name": customer_name})
    profile = json.loads(profile_out.content) if not profile_out.is_error else None
    if profile is None or not profile.get("found"):
        # not found / ambiguous: pass the tool's own payload straight through
        return profile_out.content, profile_out.is_error

    issues_out = await dispatch("get_open_issues", {"customer_name": profile["name"]})
    if issues_out.is_error:
        return issues_out.content, True  # pass the tool's own message through
    issues = json.loads(issues_out.content)
    open_issues = issues.get("issues", [])

    histories: list[dict[str, Any]] = []
    if open_issues:
        outs = await asyncio.gather(
            *(
                dispatch("summarise_issue_history", {"issue_id": i["issue_id"]})
                for i in open_issues[:MAX_HISTORIES]
            )
        )
        histories = [json.loads(o.content) for o in outs if not o.is_error]
    timings["gather_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # ── stage 2: reason (one structured call, SKILL.md body as instructions) ──
    t0 = time.perf_counter()
    gathered = json.dumps(
        {"profile": profile, "open_issues": issues, "issue_histories": histories}
    )
    reason_input = [{"role": "user", "content": f"Customer data (verbatim):\n{gathered}"}]
    result = await llm.respond(
        SKILL.body, reason_input, tools=[],
        output_schema={"name": "escalation_summary", "schema": OUTPUT_SCHEMA},
    )
    timings["reason_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # ── stage 3: validate, one repair attempt ──
    try:
        summary = EscalationSummary.model_validate_json(result.output_text)
    except ValidationError as exc:
        reason_input.append({"role": "assistant", "content": result.output_text})
        reason_input.append(
            {"role": "user",
             "content": f"Your output failed validation: {exc}. Emit corrected JSON only."}
        )
        repair = await llm.respond(
            SKILL.body, reason_input, tools=[],
            output_schema={"name": "escalation_summary", "schema": OUTPUT_SCHEMA},
        )
        try:
            summary = EscalationSummary.model_validate_json(repair.output_text)
        except ValidationError:
            return "Skill failed: output did not conform to schema after one repair attempt.", True

    # ── stage 4: persist (optional; the registry re-enforces admin) ──
    persisted: dict[str, Any] = {"requested": persist}
    if persist and open_issues:
        target = open_issues[0]  # riskiest: list arrives priority-ordered
        out = await dispatch(
            "create_next_action",
            {"issue_id": target["issue_id"], "action": summary.recommended_next_action},
        )
        outcome_label = {"deny": "denied", "allow": "created"}.get(out.decision, "failed")
        if outcome_label == "created" and out.is_error:
            outcome_label = "failed"  # an errored write must never be reported as created
        persisted.update({"outcome": outcome_label, "detail": out.content[:300]})
    elif persist:
        persisted["outcome"] = "skipped_no_open_issues"

    payload = {
        "skill": SKILL.name,
        "skill_version": SKILL.version,
        "customer": profile["name"],
        **summary.model_dump(),
        "persisted_next_action": persisted,
        "stage_timings_ms": timings,
        "usage": result.usage,
    }
    return json.dumps(payload), False
