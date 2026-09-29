import asyncio
import json

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from app import providers
from app.core.errors import ErrorCode


def _client_factory(transport: MockTransport):
    def create_client(**kwargs):
        return AsyncClient(transport=transport, **kwargs)

    return create_client


def test_completion_forwards_openai_compatible_request(monkeypatch) -> None:
    captured: dict = {}

    async def handler(request: Request) -> Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads((await request.aread()).decode())
        return Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hello"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    transport = MockTransport(handler)
    monkeypatch.setattr(providers.httpx, "AsyncClient", _client_factory(transport))

    result = asyncio.run(
        providers.completion(
            [{"role": "user", "content": "hi"}],
            model="upstream-model",
            tools=[{"type": "function", "function": {"name": "status"}}],
            provider={"base_url": "https://upstream.test/v1", "api_key": "test-key"},
        )
    )

    assert captured == {
        "url": "https://upstream.test/v1/chat/completions",
        "authorization": "Bearer test-key",
        "payload": {
            "model": "upstream-model",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "status"}}],
            "tool_choice": "auto",
        },
    }
    assert result == {
        "role": "assistant",
        "content": "hello",
        "_usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }


def test_workspace_endpoint_never_inherits_global_llm_key(monkeypatch) -> None:
    monkeypatch.setattr(providers.settings, "llm_api_key", "platform-global-key")
    called = False

    def forbidden_client(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("request must fail before constructing a client")

    monkeypatch.setattr(providers.httpx, "AsyncClient", forbidden_client)

    with pytest.raises(providers.ProviderNotConfiguredError):
        asyncio.run(
            providers.completion(
                [{"role": "user", "content": "hi"}],
                provider={
                    "base_url": "https://attacker.example.test/v1",
                    "api_key": "",
                    "model": "demo",
                },
            )
        )
    assert called is False


def test_workspace_stream_never_inherits_global_llm_key(monkeypatch) -> None:
    monkeypatch.setattr(providers.settings, "llm_api_key", "platform-global-key")

    async def consume() -> None:
        async for _ in providers.stream_chat(
            [{"role": "user", "content": "hi"}],
            provider={
                "base_url": "https://attacker.example.test/v1",
                "model": "demo",
            },
        ):
            raise AssertionError("unreachable")

    with pytest.raises(providers.ProviderNotConfiguredError):
        asyncio.run(consume())


def test_stream_accepts_keepalive_and_usage_only_chunks(monkeypatch) -> None:
    captured: dict = {}

    async def handler(request: Request) -> Response:
        captured["payload"] = json.loads((await request.aread()).decode())
        content = """\
: keepalive

data: {"choices":[{"delta":{"content":"hello"}}]}

data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2}}

data: {"choices":[{"delta":{"content":" world"}}]}

data: [DONE]

"""
        return Response(200, headers={"content-type": "text/event-stream"}, content=content)

    transport = MockTransport(handler)
    monkeypatch.setattr(providers.httpx, "AsyncClient", _client_factory(transport))
    usages: list[dict] = []

    async def consume() -> list[str]:
        return [
            chunk
            async for chunk in providers.stream_chat(
                [{"role": "user", "content": "hi"}],
                provider={
                    "base_url": "https://upstream.test/v1",
                    "api_key": "test-key",
                    "model": "upstream-model",
                },
                usage_callback=usages.append,
            )
        ]

    assert asyncio.run(consume()) == ["hello", " world"]
    assert usages == [{"prompt_tokens": 3, "completion_tokens": 2}]
    assert captured["payload"] == {
        "model": "upstream-model",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }


def test_minimax_requests_split_reasoning_from_final_content(monkeypatch) -> None:
    captured: dict = {}

    async def handler(request: Request) -> Response:
        captured["payload"] = json.loads((await request.aread()).decode())
        return Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "reasoning_content": "private reasoning",
                            "content": "clean answer",
                        }
                    }
                ]
            },
        )

    transport = MockTransport(handler)
    monkeypatch.setattr(providers.httpx, "AsyncClient", _client_factory(transport))

    result = asyncio.run(
        providers.completion(
            [{"role": "user", "content": "hi"}],
            provider={
                "base_url": "https://api.minimaxi.com/v1",
                "api_key": "test-key",
                "model": "MiniMax-M3",
            },
        )
    )

    assert captured["payload"]["reasoning_split"] is True
    assert result["content"] == "clean answer"
    assert result["reasoning_content"] == "private reasoning"


def test_provider_http_error_uses_stable_application_error(monkeypatch) -> None:
    def handler(_request: Request) -> Response:
        return Response(429, json={"error": {"message": "rate limited"}})

    transport = MockTransport(handler)
    monkeypatch.setattr(providers.httpx, "AsyncClient", _client_factory(transport))

    async def run() -> None:
        try:
            await providers.completion(
                [{"role": "user", "content": "hi"}],
                provider={"base_url": "https://upstream.test/v1", "api_key": "test-key"},
            )
        except providers.LLMError as exc:
            assert str(exc).startswith("LLM request failed:")
            assert exc.code == ErrorCode.UPSTREAM_REQUEST_FAILED
            assert exc.retryable
        else:
            raise AssertionError("Expected LLMError")

    asyncio.run(run())
