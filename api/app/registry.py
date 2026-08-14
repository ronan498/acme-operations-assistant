"""Tool registry — every model-requested tool call passes three gates:

    1. validate   — is this a known tool with parseable arguments?
    2. authorize  — does the caller's role permit it? (policy.py, fail-closed)
    3. call       — dispatch over MCP, cap the result size

Denials come back as ERROR TOOL RESULTS, never exceptions: the model gets
to read why and explain itself to the user. Every decision — allow and
deny — is audited.

Fail-closed metadata: a tool discovered from MCP that has no annotation
here is treated as a destructive write (not read-only, not concurrency-
safe) and policy.py already requires admin for unregistered tools.
"""

import json
import time
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from . import audit
from .auth import Principal
from .config import settings
from .policy import authorize


@dataclass(frozen=True)
class ToolMeta:
    read_only: bool = False        # fail-closed: assume it writes
    concurrency_safe: bool = False # fail-closed: assume it must serialise
    max_result_chars: int = 8_000  # cap what flows into model context
    inject_actor: bool = False     # server sets args["actor"] from the Principal;
                                   # the param is stripped from the model's schema —
                                   # authorship cannot be spoofed by the model


_READ = ToolMeta(read_only=True, concurrency_safe=True)
_WRITE = ToolMeta(inject_actor=True)

ANNOTATIONS: dict[str, ToolMeta] = {
    "get_customer_profile": _READ,
    "get_open_issues": _READ,
    "summarise_issue_history": _READ,
    "add_issue_update": _WRITE,
    "create_next_action": _WRITE,
    "update_next_action": _WRITE,
    # reads for everyone; its persist stage re-enters dispatch for the
    # admin-gated write. Not concurrency_safe: it makes its own LLM call.
    "customer_escalation_summary": ToolMeta(read_only=True, max_result_chars=16_000),
}

# Local tools run in-process (they orchestrate other tools + LLM calls);
# they pass the SAME three gates as MCP tools. Tier-1 skill frontmatter
# becomes the tool description the agent sees.
def _skill_schema() -> dict[str, Any]:
    from .skillloader import load_skill

    skill = load_skill("customer_escalation_summary")
    return {
        "type": "function",
        "name": skill.name,
        "description": skill.tool_description,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["customer_name", "persist_next_action"],
            "properties": {
                "customer_name": {"type": "string", "description": "Customer to assess"},
                "persist_next_action": {
                    "type": "boolean",
                    "description": "true ONLY if the user explicitly asked to save/record the recommended action",
                },
            },
        },
        "strict": True,
    }

_FAIL_CLOSED_META = ToolMeta()


@dataclass
class ToolOutcome:
    tool: str
    content: str
    is_error: bool
    decision: str          # allow | deny | error
    latency_ms: float


class ToolRegistry:
    def __init__(self) -> None:
        self._schemas: list[dict[str, Any]] | None = None

    def meta(self, tool: str) -> ToolMeta:
        return ANNOTATIONS.get(tool, _FAIL_CLOSED_META)

    async def openai_schemas(self) -> list[dict[str, Any]]:
        """Tool schemas discovered from the MCP server at runtime — the agent
        holds no tool definitions of its own (§4.2). Sorted by name so the
        serialized prompt prefix is byte-stable for provider-side caching."""
        if self._schemas is None:
            async with streamablehttp_client(settings.mcp_server_url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
            schemas = []
            for tool in sorted(listing.tools, key=lambda t: t.name):
                params = dict(tool.inputSchema)
                if self.meta(tool.name).inject_actor:
                    # the model never sees (or controls) the actor param
                    params["properties"] = {
                        k: v for k, v in params.get("properties", {}).items() if k != "actor"
                    }
                    params["required"] = [r for r in params.get("required", []) if r != "actor"]
                params["additionalProperties"] = False
                props = list(params.get("properties", {}))
                # strict mode requires every property to be required
                strict = sorted(params.get("required", [])) == sorted(props)
                # Responses-API tool shape: flat, not nested under "function"
                schemas.append(
                    {
                        "type": "function",
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": params,
                        "strict": strict,
                    }
                )
            schemas.append(_skill_schema())
            self._schemas = sorted(schemas, key=lambda s: s["name"])
        return self._schemas

    async def dispatch(self, principal: Principal, tool: str, arguments_json: str) -> ToolOutcome:
        start = time.perf_counter()

        def _done(content: str, is_error: bool, decision: str, args: dict) -> ToolOutcome:
            return ToolOutcome(tool, content, is_error, decision,
                               round((time.perf_counter() - start) * 1000, 1))

        # gate 1 — validate
        try:
            args = json.loads(arguments_json or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
        except (json.JSONDecodeError, ValueError) as exc:
            return _done(f"Invalid tool arguments: {exc}", True, "error", {})

        # gate 2 — authorize (fail-closed), audited either way
        decision = authorize(principal, tool)
        await audit.record(
            principal, tool=tool, args=args,
            decision="allow" if decision.allow else "deny", reason=decision.reason,
        )
        if not decision.allow:
            return _done(
                f"Permission denied: {decision.reason}. "
                "Explain this to the user plainly and suggest a user with the required role.",
                True, "deny", args,
            )

        if self.meta(tool).inject_actor:
            args = {**args, "actor": principal.username}  # server-authoritative authorship

        # gate 3 — local tools (Skills) run in-process, same gates already passed
        if tool == "customer_escalation_summary":
            from .escalation import run_skill

            try:
                content, is_error = await run_skill(principal, args)
            except Exception as exc:  # noqa: BLE001
                return _done(f"Skill failed: {type(exc).__name__}: {exc}", True, "error", args)
            cap = self.meta(tool).max_result_chars
            if len(content) > cap:
                content = content[:cap] + f"\n[truncated at {cap} chars]"
            return _done(content, is_error, "allow", args)

        # gate 3 — call over MCP, cap the result
        try:
            async with streamablehttp_client(settings.mcp_server_url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, arguments=args)
        except Exception as exc:  # noqa: BLE001 — the model should see tool failures
            return _done(f"Tool execution failed: {type(exc).__name__}: {exc}", True, "error", args)

        text = "\n".join(
            block.text for block in result.content if getattr(block, "type", "") == "text"
        )
        cap = self.meta(tool).max_result_chars
        if len(text) > cap:
            text = text[:cap] + f"\n[truncated at {cap} chars — narrow the query]"
        return _done(text, bool(result.isError), "allow", args)


registry = ToolRegistry()
