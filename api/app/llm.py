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


@dataclass
class LLMResult:
    output: list[Any]                 # response output items (reasoning, function_call, message)
    output_text: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)


class LLMService:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    @staticmethod
    def _delay(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        base = min(BASE_DELAY_S * (2 ** (attempt - 1)), MAX_DELAY_S)
        return base + random.random() * 0.25 * base

    async def respond(
        self,
        instructions: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResult:
        model = settings.primary_model
        attempt = 0
        while True:
            attempt += 1
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "instructions": instructions,
                    "input": input_items,
                }
                if tools:
                    kwargs["tools"] = tools
                resp = await self._client.responses.create(**kwargs)

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
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                # A 429 is not always transient: billing/quota exhaustion is a
                # permanent condition wearing a rate-limit status code.
                if (getattr(exc, "code", None) or "") in NEVER_RETRY_CODES:
                    raise
                if attempt > MAX_RETRIES:
                    raise
                retry_after = None
                if isinstance(exc, RateLimitError):
                    retry_after = (exc.response.headers or {}).get("retry-after")
                await asyncio.sleep(self._delay(attempt, retry_after))
            except APIStatusError as exc:
                if exc.status_code >= 500 and attempt <= MAX_RETRIES:
                    await asyncio.sleep(self._delay(attempt, None))
                    continue
                raise  # 4xx: our request is wrong — retrying cannot fix it


llm = LLMService()
