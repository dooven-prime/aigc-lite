"""Small provider abstraction for OpenAI-compatible chat APIs."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from .config import settings
from .core.errors import LLMError as LLMError
from .core.errors import (
    ProviderNotConfiguredError,
    UpstreamRequestError,
    UpstreamResponseError,
)


async def completion(
    messages: list[dict[str, Any]],
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    provider: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = provider or {}
    api_key = provider.get("api_key") or settings.llm_api_key
    base_url = provider.get("base_url") or settings.llm_base_url
    if not api_key:
        raise ProviderNotConfiguredError("AIGC_LITE_LLM_API_KEY is not configured")
    payload: dict[str, Any] = {"model": model or provider.get("model") or settings.llm_model, "messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=settings.llm_timeout) as client:
            response = await client.post("/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise UpstreamRequestError(f"LLM request failed: {exc}") from exc
    data: dict[str, Any] = response.json()
    try:
        message = dict(data["choices"][0]["message"])
        message["_usage"] = data.get("usage") or {}
        return message
    except (KeyError, IndexError, TypeError) as exc:
        raise UpstreamResponseError("LLM returned an invalid response") from exc


async def chat(messages: list[dict[str, Any]], model: str | None = None, provider: dict[str, Any] | None = None) -> str:
    message = await completion(messages, model, provider=provider)
    return message.get("content") or ""


async def stream_chat(
    messages: list[dict[str, Any]],
    model: str | None = None,
    provider: dict[str, Any] | None = None,
    usage_callback: Callable[[dict[str, Any]], None] | None = None,
) -> AsyncIterator[str]:
    """Yield text chunks from a provider's SSE response."""
    provider = provider or {}
    api_key = provider.get("api_key") or settings.llm_api_key
    base_url = provider.get("base_url") or settings.llm_base_url
    if not api_key:
        raise ProviderNotConfiguredError("AIGC_LITE_LLM_API_KEY is not configured")
    payload = {
        "model": model or provider.get("model") or settings.llm_model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with (
            httpx.AsyncClient(base_url=base_url, timeout=settings.llm_timeout) as client,
            client.stream("POST", "/chat/completions", json=payload, headers=headers) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                data = json.loads(payload)
                if data.get("usage") and usage_callback:
                    usage_callback(data["usage"])
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                if delta.get("content"):
                    yield delta["content"]
    except (httpx.HTTPError, json.JSONDecodeError, IndexError, TypeError) as exc:
        raise UpstreamRequestError(f"LLM stream failed: {exc}") from exc
