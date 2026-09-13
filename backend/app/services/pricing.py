"""Standard text-token estimates; never infer pricing for unknown models or tiers."""

from decimal import Decimal


def pricing_snapshot(provider, model, service_tier):
    if (
        provider != "openai"
        or service_tier != "default"
        or model not in {"gpt-4.1-mini", "gpt-4.1-mini-2025-04-14"}
    ):
        return None
    return {
        "currency": "USD",
        "input_per_million": "0.40",
        "cached_input_per_million": "0.10",
        "output_per_million": "1.60",
        "service_tier": "default",
        "source": "https://developers.openai.com/api/docs/models/gpt-4.1-mini",
        "verified_on": "2026-09-12",
    }


def estimate_cost(pricing, input_tokens, output_tokens, cached_input_tokens):
    if pricing is None or any(
        v is None for v in (input_tokens, output_tokens, cached_input_tokens)
    ):
        return None
    return (
        Decimal(input_tokens - cached_input_tokens)
        * Decimal(pricing["input_per_million"])
        + Decimal(cached_input_tokens) * Decimal(pricing["cached_input_per_million"])
        + Decimal(output_tokens) * Decimal(pricing["output_per_million"])
    ) / Decimal(1_000_000)
