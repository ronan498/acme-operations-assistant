from app.llm import CONSECUTIVE_BEFORE_FALLBACK, MAX_TOTAL_ATTEMPTS, FallbackState


def make() -> FallbackState:
    return FallbackState(models=["gpt-5.6-sol", "gpt-5.5", "gpt-5.4"])


def test_never_falls_back_on_first_error() -> None:
    """Eager fallback is a cache-thrash bug wearing a resilience costume."""
    s = make()
    assert s.on_capacity_error() == "retry"
    assert s.model == "gpt-5.6-sol"


def test_falls_back_after_consecutive_failures() -> None:
    s = make()
    for _ in range(CONSECUTIVE_BEFORE_FALLBACK - 1):
        assert s.on_capacity_error() == "retry"
    assert s.on_capacity_error() == "fallback"
    assert s.model == "gpt-5.5"


def test_success_resets_the_streak() -> None:
    """One good response on the primary means the streak starts over —
    intermittent blips never walk down the chain."""
    s = make()
    s.on_capacity_error()
    s.on_capacity_error()
    s.on_success()
    assert s.on_capacity_error() == "retry"
    assert s.model == "gpt-5.6-sol"


def test_chain_exhausts_at_the_last_model() -> None:
    s = make()
    while (action := s.on_capacity_error()) != "exhausted":
        assert action in ("retry", "fallback")
    assert s.total_attempts <= MAX_TOTAL_ATTEMPTS


def test_total_attempt_ceiling_holds_even_with_models_left() -> None:
    s = FallbackState(models=["a", "b", "c", "d", "e", "f"])
    actions = [s.on_capacity_error() for _ in range(MAX_TOTAL_ATTEMPTS)]
    assert actions[-1] == "exhausted"
