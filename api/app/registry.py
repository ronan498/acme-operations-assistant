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


ANNOTATIONS: dict[str, ToolMeta] = {
    "get_customer_profile": ToolMeta(read_only=True, concurrency_safe=True),
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
                params["additionalProperties"] = False
                props = list(params.get("properties", {}))
                # strict mode requires every property to be required
                strict = sorted(params.get("required", [])) == sorted(props)
                fn: dict[str, Any] = {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": params,
                }
                if strict:
                    fn["strict"] = True
                schemas.append({"type": "function", "function": fn})
            self._schemas = schemas
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
