"""OpenAI-compatible adapter tests using httpx MockTransport (no network)."""

import httpx
import pytest

from xenopus.provider.openai_compat import OpenAICompatProvider
from xenopus.provider.types import (
    AuthError,
    CompletionRequest,
    Message,
    ProviderUnavailableError,
    RateLimitError,
    Role,
    SchemaError,
)


def make_request(content: str = "ping") -> CompletionRequest:
    return CompletionRequest(
        model="test-model",
        messages=(Message(role=Role.USER, content=content),),
        correlation_id="corr-test",
    )


def make_provider(transport: httpx.MockTransport) -> OpenAICompatProvider:
    client = httpx.AsyncClient(transport=transport)
    return OpenAICompatProvider(
        base_url="https://api.example.com/v1",
        api_key="test-key-000000000000",
        model="test-model",
        name="test",
        client=client,
    )


def ok_body(content: str = "pong") -> dict[str, object]:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "model": "test-model",
        "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
    }


class TestOpenAICompat:
    @pytest.mark.asyncio
    async def test_success_parses_content_and_usage(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=ok_body("hello there"))

        provider = make_provider(httpx.MockTransport(handler))
        response = await provider.complete(make_request())
        assert response.content == "hello there"
        assert response.usage.total_tokens == 6
        assert response.model == "test-model"

    @pytest.mark.asyncio
    async def test_auth_header_sent(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json=ok_body())

        provider = make_provider(httpx.MockTransport(handler))
        await provider.complete(make_request())
        assert captured["auth"] == "Bearer test-key-000000000000"

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "bad key"})

        provider = make_provider(httpx.MockTransport(handler))
        with pytest.raises(AuthError, match="auth rejected"):
            await provider.complete(make_request())

    @pytest.mark.asyncio
    async def test_429_raises_rate_limit_with_retry_after(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "17"}, json={"error": "slow down"})

        provider = make_provider(httpx.MockTransport(handler))
        with pytest.raises(RateLimitError) as excinfo:
            await provider.complete(make_request())
        assert excinfo.value.retry_after_seconds == 17.0

    @pytest.mark.asyncio
    async def test_500_raises_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        provider = make_provider(httpx.MockTransport(handler))
        with pytest.raises(ProviderUnavailableError, match="server error"):
            await provider.complete(make_request())

    @pytest.mark.asyncio
    async def test_network_error_raises_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        provider = make_provider(httpx.MockTransport(handler))
        with pytest.raises(ProviderUnavailableError, match="request failed"):
            await provider.complete(make_request())

    @pytest.mark.asyncio
    async def test_malformed_200_raises_schema_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": "junk"})

        provider = make_provider(httpx.MockTransport(handler))
        with pytest.raises(SchemaError, match="choices"):
            await provider.complete(make_request())

    @pytest.mark.asyncio
    async def test_missing_content_raises_schema_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = {"choices": [{"message": {"role": "assistant"}}], "model": "m"}
            return httpx.Response(200, json=body)

        provider = make_provider(httpx.MockTransport(handler))
        with pytest.raises(SchemaError, match="content"):
            await provider.complete(make_request())

    def test_empty_api_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            OpenAICompatProvider(
                base_url="https://api.example.com/v1",
                api_key="",
                model="m",
                name="x",
            )

    def test_bad_base_url_rejected(self) -> None:
        with pytest.raises(ValueError, match="http"):
            OpenAICompatProvider(
                base_url="ftp://example.com",
                api_key="k",
                model="m",
                name="x",
            )
