"""Acme MCP tool server — Phase 0 skeleton.

Boots as a genuine MCP server over streamable HTTP with zero tools.
Tool definitions live HERE, never in the agent: the api discovers them
at runtime via list_tools (the §4.2 separation). Tools arrive in Phase 3+.
"""

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

mcp = FastMCP("acme-tools", host="0.0.0.0", port=8765)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
