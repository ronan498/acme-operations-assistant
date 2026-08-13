from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import readiness
from .config import settings

app = FastAPI(
    title="Acme Operations Assistant",
    description="Agentic enterprise assistant — EY Applied AI Engineer case study",
    version="0.1.0",
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
