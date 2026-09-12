from decimal import Decimal

import pytest
from app.services.llm_calls import CallResult
from app.services.pricing import estimate_cost, pricing_snapshot


def test_cached_tokens_and_snapshot():
    rates = pricing_snapshot("openai", "gpt-4.1-mini", "default")
    assert estimate_cost(rates, 1000, 500, 200) == Decimal("0.00114")
    rates["input_per_million"] = "999"
    assert (
        pricing_snapshot("openai", "gpt-4.1-mini", "default")["input_per_million"]
        == "0.40"
    )


def test_unknown_is_distinct_from_zero():
    rates = pricing_snapshot("openai", "gpt-4.1-mini", "default")
    assert estimate_cost(rates, 0, 0, 0) == 0
    assert estimate_cost(rates, 10, 20, None) is None
    assert estimate_cost(None, 10, 20, 0) is None
    assert pricing_snapshot("openai", "unknown", "default") is None
    assert pricing_snapshot("openai", "gpt-4.1-mini", "priority") is None


@pytest.mark.parametrize("input_tokens,cached", [(None, 0), (1, 2), (1, -1), (1, True)])
def test_invalid_cached_tokens(input_tokens, cached):
    with pytest.raises(ValueError):
        CallResult().set_result(
            "text", input_tokens=input_tokens, cached_input_tokens=cached
        )
