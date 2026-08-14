"""Tracing + structured logging.

OTel spans export to Phoenix over OTLP/HTTP. OpenInference auto-
instruments the OpenAI client (LLM spans with prompts, tokens, latency);
the registry adds a span per tool dispatch; /chat opens the root span.
The root trace_id is returned to the caller — paste it into Phoenix and
the waterfall for that exact request appears.
"""

import json
import logging
import sys
from datetime import UTC, datetime

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .config import settings

log = logging.getLogger("acme")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            entry["trace_id"] = format(ctx.trace_id, "032x")
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        for key in ("tool", "decision", "latency_ms", "user", "session_id", "model"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        return json.dumps(entry)


def setup() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    provider = TracerProvider(
        resource=Resource.create({"service.name": "acme-api"})
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{settings.phoenix_collector_endpoint}/v1/traces")
        )
    )
    trace.set_tracer_provider(provider)

    try:
        from openinference.instrumentation.openai import OpenAIInstrumentor

        OpenAIInstrumentor().instrument(tracer_provider=provider)
        log.info("openinference OpenAI instrumentation active")
    except Exception as exc:  # noqa: BLE001 — tracing must never take the api down
        log.warning(f"OpenAI auto-instrumentation unavailable: {type(exc).__name__}: {exc}")


def tracer() -> trace.Tracer:
    return trace.get_tracer("acme-api")


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
