"""Small provider abstraction for OpenAI-compatible chat APIs."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from .config import settings
from .core.errors import LLMError as LLMError
from .core.errors import (
    ProviderNotConfiguredError,
    UpstreamRequestError,
    UpstreamResponseError,
)

_MINIMAX_HOSTS = {"api.minimax.io", "api.minimaxi.com"}


def validate_workspace_model_base_url(value: str) -> str:
    """Validate a workspace-controlled OpenAI-compatible HTTPS endpoint."""

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("base_url contains an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "base_url must be HTTPS without credentials, query, or fragment"
        )
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("base_url contains an invalid port")
    decoded_path = unquote(parsed.path)
    if "\\" in decoded_path or any(
        segment in {".", ".."} for segment in decoded_path.split("/")
    ):
        raise ValueError("base_url path must be canonical")
    if any(ord(character) < 32 for character in value):
        raise ValueError("base_url contains control characters")
    return value.rstrip("/")


def _provider_connection(
    provider: dict[str, Any] | None,
) -> tuple[str, str, dict[str, Any]]:
    if provider is None:
        api_key = settings.llm_api_key
        base_url = settings.llm_base_url
        values: dict[str, Any] = {}
    else:
        values = provider
        api_key = values.get("api_key")
        base_url = values.get("base_url")
        if not isinstance(api_key, str) or not api_key:
            raise ProviderNotConfiguredError(
                "Selected workspace model has no bound credential"
            )
        if not isinstance(base_url, str) or not base_url:
            raise ProviderNotConfiguredError(
                "Selected workspace model has no bound endpoint"
            )
        try:
            base_url = validate_workspace_model_base_url(base_url)
        except ValueError as exc:
            raise ProviderNotConfiguredError(
                "Selected workspace model endpoint is invalid"
            ) from exc
    if not api_key:
        raise ProviderNotConfiguredError("AIGC_LITE_LLM_API_KEY is not configured")
    return api_key, base_url, values


def _provider_payload(base_url: str) -> dict[str, Any]:
    """Return narrowly scoped compatibility flags for known providers."""
    hostname = urlsplit(base_url).hostname
    if hostname in _MINIMAX_HOSTS:
        # MiniMax otherwise embeds <think> blocks into content. Keeping its
        # reasoning field separate preserves clean Agent answers and tool loops.
        return {"reasoning_split": True}
    return {}


async def completion(
    messages: list[dict[str, Any]],
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    provider: dict[str, Any] | None = None,
) -> dict[str, Any]:
    api_key, base_url, provider_values = _provider_connection(provider)
    payload: dict[str, Any] = {
        "model": model or provider_values.get("model") or settings.llm_model,
        "messages": messages,
        **_provider_payload(base_url),
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=settings.llm_timeout,
            follow_redirects=False,
        ) as client:
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
    api_key, base_url, provider_values = _provider_connection(provider)
    payload = {
        "model": model or provider_values.get("model") or settings.llm_model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        **_provider_payload(base_url),
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with (
            httpx.AsyncClient(
                base_url=base_url,
                timeout=settings.llm_timeout,
                follow_redirects=False,
            ) as client,
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


async def stream_completion(
    messages: list[dict[str, Any]],
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    provider: dict[str, Any] | None = None,
    *,
    on_delta: Callable[[str], Awaitable[None]],
) -> dict[str, Any]:
    """Stream one model turn, assembling tool calls before the shared Agent loop acts."""
    api_key, base_url, values = _provider_connection(provider)
    payload: dict[str, Any] = {
        "model": model or values.get("model") or settings.llm_model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        **_provider_payload(base_url),
    }
    if tools:
        payload.update(tools=tools, tool_choice="auto")
    content: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] = {}
    done = False
    try:
        async with (
            httpx.AsyncClient(
                base_url=base_url, timeout=settings.llm_timeout, follow_redirects=False
            ) as client,
            client.stream(
                "POST", "/chat/completions", json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            ) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                item = line[6:]
                if item == "[DONE]":
                    done = True
                    break
                data = json.loads(item)
                if not isinstance(data, dict):
                    raise UpstreamResponseError("LLM returned an invalid stream event")
                if isinstance(data.get("usage"), dict):
                    usage = data["usage"]
                choices = data.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if not isinstance(choice, dict) or not isinstance(choice.get("delta"), dict):
                    raise UpstreamResponseError("LLM returned an invalid stream delta")
                if choice.get("finish_reason") == "length":
                    raise UpstreamResponseError("LLM truncated a streamed model turn")
                delta = choice["delta"]
                fragment = delta.get("content")
                if fragment is not None:
                    if not isinstance(fragment, str):
                        raise UpstreamResponseError("LLM returned invalid text content")
                    if fragment:
                        content.append(fragment)
                        await on_delta(fragment)
                for part in delta.get("tool_calls") or []:
                    if not isinstance(part, dict) or type(part.get("index")) is not int:
                        raise UpstreamResponseError("LLM returned an invalid tool-call index")
                    index = part["index"]
                    if index < 0 or index > 255:
                        raise UpstreamResponseError("LLM returned an invalid tool-call index")
                    call = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    for field in ("id", "type"):
                        piece = part.get(field)
                        if piece is not None:
                            if not isinstance(piece, str):
                                raise UpstreamResponseError("LLM returned an invalid tool call")
                            if field == "type" and piece != "function":
                                raise UpstreamResponseError("LLM returned an unsupported tool call")
                            if field == "id":
                                call[field] += piece
                                if len(call[field]) > 256:
                                    raise UpstreamResponseError("LLM tool-call identity is too long")
                    function = part.get("function") or {}
                    if not isinstance(function, dict):
                        raise UpstreamResponseError("LLM returned an invalid tool call")
                    for field in ("name", "arguments"):
                        piece = function.get(field)
                        if piece is not None:
                            if not isinstance(piece, str):
                                raise UpstreamResponseError("LLM returned an invalid tool call")
                            call["function"][field] += piece
                            if len(call["function"][field]) > (
                                256 if field == "name" else 65_536
                            ):
                                raise UpstreamResponseError("LLM tool-call payload is too long")
    except (httpx.HTTPError, json.JSONDecodeError, IndexError, TypeError) as exc:
        raise UpstreamRequestError(f"LLM stream failed: {exc}") from exc
    if not done:
        raise UpstreamResponseError("LLM stream ended before completion")
    ordered = [calls[index] for index in sorted(calls)]
    if any(not call["id"] or not call["function"]["name"] for call in ordered):
        raise UpstreamResponseError("LLM returned an incomplete tool call")
    return {
        "role": "assistant", "content": "".join(content),
        "tool_calls": ordered, "_usage": usage,
    }
