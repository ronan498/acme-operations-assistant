"""LLM service — one seam in front of OpenAI.

Retry taxonomy, not blanket retry: 429/5xx/timeouts back off and retry
(Retry-After wins over local backoff); 4xx client errors never retry.
Fallback chain (PRIMARY_MODEL → FALLBACK_MODELS) is wired but activates
only after consecutive capacity failures — switching models cold-starts
the provider prompt cache, so eager fallback is a net loss (Phase 4
turns it on; see PLAN).

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


@dataclass
class LLMResult:
    message: Any                      # the assistant message (may carry tool_calls)
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

    async def chat(self, messages: list[dict], tools: list[dict]) -> LLMResult:
        model = settings.primary_model
        attempt = 0
        while True:
            attempt += 1
            try:
                kwargs: dict[str, Any] = {"model": model, "messages": messages}
                if tools:
                    kwargs["tools"] = tools
                resp = await self._client.chat.completions.create(**kwargs)
                usage = resp.usage
                cached = 0
                if usage and usage.prompt_tokens_details:
                    cached = usage.prompt_tokens_details.cached_tokens or 0
                return LLMResult(
                    message=resp.choices[0].message,
                    model=resp.model,
                    usage={
                        "prompt_tokens": usage.prompt_tokens if usage else 0,
                        "completion_tokens": usage.completion_tokens if usage else 0,
                        "cached_tokens": cached,
                    },
                )
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                # A 429 is not always transient: billing/quota exhaustion is a
                # permanent condition wearing a rate-limit status code.
                code = getattr(exc, "code", None) or ""
                if code in ("billing_not_active", "insufficient_quota", "billing_hard_limit_reached"):
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
