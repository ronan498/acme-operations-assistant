from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import audit, readiness, telemetry
from .agent import run_agent
from .costs import totals
from .auth import Principal, get_principal
from .config import settings
from .policy import authorize


@asynccontextmanager
async def lifespan(_: FastAPI):
    telemetry.setup()
    yield
    await audit.close_pool()


app = FastAPI(
    title="Acme Operations Assistant",
    description="Agentic enterprise assistant — EY Applied AI Engineer case study",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    """Liveness: the process is up. Says nothing about dependencies."""
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness: every downstream dependency answers, with per-check latency."""
    all_up, checks = await readiness.run_all()
    return JSONResponse(
        status_code=200 if all_up else 503,
        content={
            "status": "ready" if all_up else "degraded",
            "checks": checks,
            "models": {
                "primary": settings.primary_model,
                "fallbacks": settings.fallback_models.split(","),
            },
        },
    )


@app.get("/me")
async def me(principal: Principal = Depends(get_principal)) -> dict:
    """Who am I, and what am I allowed to do? Demo aid and RBAC probe."""
    return {
        "sub": principal.sub,
        "username": principal.username,
        "roles": sorted(principal.roles),
    }


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


@app.post("/chat")
async def chat(
    body: ChatRequest, principal: Principal = Depends(get_principal)
) -> dict:
    """The agent: auth → ReAct loop → registry-gated tools → grounded answer."""
    with telemetry.tracer().start_as_current_span("chat") as span:
        span.set_attributes(
            {"user.name": principal.username, "session.id": body.session_id}
        )
        trace_id = telemetry.current_trace_id()
        try:
            result = await run_agent(principal, body.session_id, body.message)
        except Exception as exc:  # noqa: BLE001 — surface upstream failures as a clean 502
            raise HTTPException(
                status_code=502, detail=f"model upstream error: {type(exc).__name__}: {exc}"
            ) from exc
        cost = totals.add(result.model, result.usage)
        span.set_attributes(
            {
                "llm.model": result.model,
                "llm.rounds": result.rounds,
                "llm.input_tokens": result.usage.get("input_tokens", 0),
                "llm.output_tokens": result.usage.get("output_tokens", 0),
                "llm.cached_tokens": result.usage.get("cached_tokens", 0),
                **({"llm.est_cost_usd": cost} if cost is not None else {}),
            }
        )
    return {
        "answer": result.answer,
        "session_id": body.session_id,
        "stop_reason": result.stop_reason,
        "rounds": result.rounds,
        "tool_calls": result.tool_calls,
        "usage": result.usage,
        "model": result.model,
        "est_cost_usd": cost,
        "trace_id": trace_id,
    }


@app.get("/stats")
async def stats(principal: Principal = Depends(get_principal)) -> dict:
    """Running totals since boot — tokens, cache hit rate, estimated spend."""
    return totals.snapshot()


# The UI: a static Vite build served same-origin. Mounted last so every API
# route above wins; anything else falls through to index.html.
_static = Path(__file__).parent.parent / "static"
if _static.is_dir():
    app.mount("/", StaticFiles(directory=_static, html=True), name="ui")


class AuthzCheck(BaseModel):
    tool: str
    args: dict = {}


@app.post("/authz/check")
async def authz_check(
    body: AuthzCheck, principal: Principal = Depends(get_principal)
) -> dict:
    """Run the real authorization path for a tool call and audit the outcome.

    This is the exact function the tool registry dispatches through from
    Phase 3 — not a mock of it. Denials return 403 so the gate is visible.
    """
    decision = authorize(principal, body.tool)
    await audit.record(
        principal,
        tool=body.tool,
        args=body.args,
        decision="allow" if decision.allow else "deny",
        reason=decision.reason,
    )
    if not decision.allow:
        raise HTTPException(status_code=403, detail=decision.reason)
    return {"tool": body.tool, "decision": "allow", "reason": decision.reason}
