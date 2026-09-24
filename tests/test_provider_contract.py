import asyncio
import json

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
