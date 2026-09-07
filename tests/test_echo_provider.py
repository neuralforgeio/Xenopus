"""Provider types + echo provider tests (fully offline)."""

import pytest

from xenopus.provider.echo import EchoProvider
from xenopus.provider.types import (
    AuthError,
    CompletionRequest,
    Message,
    RateLimitError,
    Role,
    Usage,
)


def req(content: str) -> CompletionRequest:
    return CompletionRequest(
        model="echo-1",
        messages=(Message(role=Role.USER, content=content),),
    )


class TestTypes:
    def test_empty_request_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one message"):
            CompletionRequest(model="m", messages=())

    def test_negative_temperature_rejected(self) -> None:
        with pytest.raises(ValueError, match="temperature"):
            CompletionRequest(
                model="m",
                messages=(Message(role=Role.USER, content="x"),),
                temperature=-0.1,
            )

    def test_usage_totals_computed(self) -> None:
        usage = Usage(prompt_tokens=10, completion_tokens=5)
        assert usage.total_tokens == 15

    def test_error_permanence_flags(self) -> None:
        assert AuthError("x").permanent is True
        assert RateLimitError("x").permanent is False


class TestEchoProvider:
    @pytest.mark.asyncio
    async def test_echo_returns_last_user_message(self) -> None:
        provider = EchoProvider()
        response = await provider.complete(req("hello world"))
        assert response.content == "echo: hello world"
        assert response.provider == "echo"
        assert response.usage.total_tokens > 0

    @pytest.mark.asyncio
    async def test_echo_uses_requested_model(self) -> None:
        provider = EchoProvider()
        response = await provider.complete(req("m"))
        assert response.model == "echo-1"

    @pytest.mark.asyncio
    async def test_echo_ignores_system_noise(self) -> None:
        provider = EchoProvider()
        request = CompletionRequest(
            model="echo-1",
            messages=(
                Message(role=Role.SYSTEM, content="system prompt"),
                Message(role=Role.USER, content="actual question"),
            ),
        )
        response = await provider.complete(request)
        assert response.content == "echo: actual question"
