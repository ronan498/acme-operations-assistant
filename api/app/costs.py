"""Cost accounting — measured tokens × pinned price table.

Prices verified against the live OpenAI pricing page 2026-08-14 (USD per
1M tokens, standard tier, short context). Override via MODEL_PRICES_JSON
env if they move. Unknown models report cost as None — never a made-up
number.
"""

import json
import os
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input_per_1m: float
    cached_input_per_1m: float
    output_per_1m: float


PRICES: dict[str, Price] = {
    "gpt-5.6-sol": Price(5.00, 0.50, 30.00),
    "gpt-5.6-terra": Price(2.00, 0.20, 12.00),
    "gpt-5.6-luna": Price(0.20, 0.02, 1.20),
    "gpt-5.5": Price(5.00, 0.50, 30.00),
    "gpt-5.4": Price(2.50, 0.25, 15.00),
}

for _model, _p in json.loads(os.environ.get("MODEL_PRICES_JSON", "{}")).items():
    PRICES[_model] = Price(**_p)


def estimate_usd(model: str, usage: dict[str, int]) -> float | None:
    """usage: input_tokens / output_tokens / cached_tokens (cached ⊆ input)."""
    price = PRICES.get(model) or PRICES.get(model.split(":")[0])
    if price is None:
        return None
    cached = usage.get("cached_tokens", 0)
    uncached_in = max(usage.get("input_tokens", 0) - cached, 0)
    return round(
        uncached_in * price.input_per_1m / 1e6
        + cached * price.cached_input_per_1m / 1e6
        + usage.get("output_tokens", 0) * price.output_per_1m / 1e6,
        6,
    )


class _Totals:
    """Running totals since boot — the demo's 'know your numbers' endpoint."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0
        self.cost_usd = 0.0
        self.unpriced_calls = 0

    def add(self, model: str, usage: dict[str, int]) -> float | None:
        cost = estimate_usd(model, usage)
        with self._lock:
            self.requests += 1
            self.input_tokens += usage.get("input_tokens", 0)
            self.output_tokens += usage.get("output_tokens", 0)
            self.cached_tokens += usage.get("cached_tokens", 0)
            if cost is None:
                self.unpriced_calls += 1
            else:
                self.cost_usd = round(self.cost_usd + cost, 6)
        return cost

    def snapshot(self) -> dict:
        with self._lock:
            hit_rate = (
                round(self.cached_tokens / self.input_tokens, 4) if self.input_tokens else 0.0
            )
            return {
                "llm_requests": self.requests,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cached_tokens": self.cached_tokens,
                "cache_hit_rate": hit_rate,
                "est_cost_usd": self.cost_usd,
                "unpriced_calls": self.unpriced_calls,
            }


totals = _Totals()
