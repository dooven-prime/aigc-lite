"""Request audit and usage accounting helpers."""

from __future__ import annotations

from .database import get_repository


def record_request(tenant_id: str, action: str, path: str, status_code: int, user_id: str | None = None) -> None:
    get_repository().write_audit(
        tenant_id, action, path, {"status_code": status_code}, user_id=user_id
    )


def record_usage(
    tenant_id: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    input_price: float = 0,
    output_price: float = 0,
) -> None:
    cost = prompt_tokens / 1000 * input_price + completion_tokens / 1000 * output_price
    get_repository().usage(tenant_id, model, prompt_tokens, completion_tokens, cost)


def usage_from_response(response_usage: dict, model: str, tenant_id: str, pricing: dict | None = None) -> None:
    """Persist provider usage while tolerating providers without token fields."""
    pricing = pricing or {}
    prompt_tokens = int(response_usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(response_usage.get("completion_tokens", 0) or 0)
    record_usage(
        tenant_id,
        model,
        prompt_tokens,
        completion_tokens,
        float(pricing.get("input_price", 0) or 0),
        float(pricing.get("output_price", 0) or 0),
    )
