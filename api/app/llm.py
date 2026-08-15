"""LLM service — one seam in front of OpenAI's Responses API.

gpt-5.6-sol is a reasoning-class model: function tools + reasoning are
only supported on /v1/responses (chat/completions returns a 400 unless
reasoning is disabled entirely — the wrong trade). Discovered live, see
AI_USAGE.md 2026-08-14.

Retry taxonomy, not blanket retry: 429/5xx/timeouts back off and retry
(Retry-After wins over local backoff); billing/quota 429s and other 4xx
never retry. Fallback chain (PRIMARY_MODEL → FALLBACK_MODELS) activates
only after consecutive capacity failures — switching models cold-starts
the provider prompt cache (Phase 4 turns it on; see PLAN).

Usage is captured per call, including cached_tokens — the measured
cache-hit signal (§4.8).
"""

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from .config import settings

MAX_RETRIES = 4
BASE_DELAY_S = 0.5
MAX_DELAY_S = 16.0
NEVER_RETRY_CODES = ("billing_not_active", "insufficient_quota", "billing_hard_limit_reached")
CONSECUTIVE_BEFORE_FALLBACK = 3  # never fall back on the first error: switching
MAX_TOTAL_ATTEMPTS = 10          # models cold-starts the provider prompt cache


@dataclass
class FallbackState:
    """Pure retry/fallback decision logic — unit-tested without SDK objects.

    Capacity errors (429/5xx/timeouts) back off on the SAME model first;
    only CONSECUTIVE_BEFORE_FALLBACK failures in a row move down the chain.
    """

    models: list[str]
    model_idx: int = 0
    consecutive_capacity: int = 0
    total_attempts: int = 0

    @property
    def model(self) -> str:
        return self.models[self.model_idx]

    def on_success(self) -> None:
        self.consecutive_capacity = 0

    def on_capacity_error(self) -> str:
        """Returns 'retry' | 'fallback' | 'exhausted'."""
        self.total_attempts += 1
        self.consecutive_capacity += 1
        if self.total_attempts >= MAX_TOTAL_ATTEMPTS:
            return "exhausted"
        if self.consecutive_capacity >= CONSECUTIVE_BEFORE_FALLBACK:
            if self.model_idx + 1 < len(self.models):
                self.model_idx += 1
                self.consecutive_capacity = 0
                return "fallback"
            return "exhausted"
        return "retry"


@dataclass
class LLMResult:
    output: list[Any]                 # response output items (reasoning, function_call, message)
    output_text: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)


class LLMService:
    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None  # lazy: no import-time side effects

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        return self._client

    @staticmethod
    def _delay(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        base = min(BASE_DELAY_S * (2 ** (attempt - 1)), MAX_DELAY_S)
        return base + random.random() * 0.25 * base

    @staticmethod
    def _result_from_response(resp: Any) -> LLMResult:
        usage: dict[str, int] = {}
        if resp.usage:
            details = resp.usage.input_tokens_details
            usage = {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
                "cached_tokens": details.cached_tokens if details else 0,
            }
        return LLMResult(
            output=list(resp.output),
            output_text=resp.output_text or "",
            model=resp.model,
            usage=usage,
        )

    async def respond_stream(
        self,
        instructions: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ):
        """Yields ("delta", text) as answer tokens arrive, then ("done", LLMResult).

        Retries/fallback apply to call INITIATION (same taxonomy as respond);
        a failure after streaming begins surfaces to the caller - replaying a
        half-consumed stream would double tool side effects.
        """
        state = FallbackState(
            models=[settings.primary_model]
            + [m.strip() for m in settings.fallback_models.split(",") if m.strip()]
        )
        attempt = 0
        while True:
            attempt += 1
            try:
                kwargs: dict[str, Any] = {
                    "model": state.model,
                    "instructions": instructions,
                    "input": input_items,
                    "stream": True,
                }
                if tools:
                    kwargs["tools"] = tools
                stream = await self.client.responses.create(**kwargs)
                break
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                if (getattr(exc, "code", None) or "") in NEVER_RETRY_CODES:
                    raise
                if state.on_capacity_error() == "exhausted":
                    raise
                retry_after = None
                if isinstance(exc, RateLimitError):
                    retry_after = (exc.response.headers or {}).get("retry-after")
                await asyncio.sleep(self._delay(attempt, retry_after))
            except APIStatusError as exc:
                if exc.status_code < 500 or state.on_capacity_error() == "exhausted":
                    raise
                await asyncio.sleep(self._delay(attempt, None))

        final = None
        async for event in stream:
            kind = getattr(event, "type", "")
            if kind == "response.output_text.delta":
                yield ("delta", event.delta)
            elif kind == "response.completed":
                final = event.response
        if final is None:
            raise RuntimeError("stream ended without response.completed")
        yield ("done", self._result_from_response(final))

    async def respond(
        self,
        instructions: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_schema: dict[str, Any] | None = None,
    ) -> LLMResult:
        state = FallbackState(
            models=[settings.primary_model]
            + [m.strip() for m in settings.fallback_models.split(",") if m.strip()]
        )
        attempt = 0
        while True:
            attempt += 1
            try:
                kwargs: dict[str, Any] = {
                    "model": state.model,
                    "instructions": instructions,
                    "input": input_items,
                }
                if tools:
                    kwargs["tools"] = tools
                if output_schema:
                    kwargs["text"] = {
                        "format": {
                            "type": "json_schema",
                            "name": output_schema["name"],
                            "schema": output_schema["schema"],
                            "strict": True,
                        }
                    }
                resp = await self.client.responses.create(**kwargs)
                return self._result_from_response(resp)
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                # A 429 is not always transient: billing/quota exhaustion is a
                # permanent condition wearing a rate-limit status code.
                if (getattr(exc, "code", None) or "") in NEVER_RETRY_CODES:
                    raise
                action = state.on_capacity_error()
                if action == "exhausted":
                    raise
                retry_after = None
                if isinstance(exc, RateLimitError):
                    retry_after = (exc.response.headers or {}).get("retry-after")
                await asyncio.sleep(0.1 if action == "fallback" else self._delay(attempt, retry_after))
            except APIStatusError as exc:
                if exc.status_code < 500:
                    raise  # 4xx: our request is wrong — retrying cannot fix it
                if state.on_capacity_error() == "exhausted":
                    raise
                await asyncio.sleep(self._delay(attempt, None))


llm = LLMService()
