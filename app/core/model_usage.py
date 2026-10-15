"""Versioned, non-authoritative accounting observations for model calls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

RECEIPT_VERSION = "model.usage.v1"
_MAX_TOKENS = 2**63 - 1


@dataclass(frozen=True, slots=True)
class NormalizedUsage:
    status: str
    prompt_tokens: int | None
    completion_tokens: int | None


def normalize_usage(value: Any) -> NormalizedUsage:
    """Unknown is never silently converted into a reported zero."""
    if value is None or value == {}:
        return NormalizedUsage("UNKNOWN", None, None)
    if not isinstance(value, dict):
        return NormalizedUsage("INVALID", None, None)
    tokens: dict[str, int | None] = {}
    invalid = False
    for field in ("prompt_tokens", "completion_tokens"):
        item = value.get(field)
        if field not in value:
            tokens[field] = None
        elif type(item) is not int or item < 0 or item > _MAX_TOKENS:
            tokens[field] = None
            invalid = True
        else:
            tokens[field] = item
    status = "INVALID" if invalid else (
        "REPORTED" if all(tokens[field] is not None for field in tokens) else "UNKNOWN"
    )
    if status == "INVALID":
        return NormalizedUsage(status, None, None)
    return NormalizedUsage(status, tokens["prompt_tokens"], tokens["completion_tokens"])


def pricing_snapshot(
    pricing: dict[str, Any] | None, *, provider_config_id: str | None,
    default_pricing_known: bool = False,
) -> dict[str, Any]:
    """Whitelist only non-secret configuration actually used for estimation."""
    values = pricing or {}
    snapshot: dict[str, Any] = {
        "version": 1,
        "unit": "per_1000_tokens",
        "source": "workspace_model_config" if provider_config_id else "deployer_defaults",
        "provider_config_id": provider_config_id,
        "price_known": bool(values.get("pricing_confirmed")) if provider_config_id else default_pricing_known,
    }
    for key in ("input_price", "output_price"):
        raw = values.get(key)
        try:
            number = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            number = None
        snapshot[key] = str(number) if number is not None and number.is_finite() and number >= 0 else None
    return snapshot


def estimated_cost(usage: NormalizedUsage, pricing: dict[str, Any]) -> str | None:
    if usage.status != "REPORTED" or not pricing.get("price_known"):
        return None
    input_price = pricing.get("input_price")
    output_price = pricing.get("output_price")
    if input_price is None or output_price is None:
        return None
    amount = (
        Decimal(usage.prompt_tokens) * Decimal(input_price)
        + Decimal(usage.completion_tokens) * Decimal(output_price)
    ) / Decimal(1000)
    return format(amount.normalize(), "f")


def canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
