"""EchoProvider: offline reference implementation of ModelProvider.

Deterministic and network-free: reflects the last user message back with a
fixed prefix. Exists so the runtime, router, and tests can exercise the
full provider stack with zero external dependencies and zero cost — the
same role a test double plays, but as a first-class local provider
(local-first default, master prompt 61/80).
"""

from xenopus.provider.types import (
    CompletionRequest,
    CompletionResponse,
    Usage,
)


class EchoProvider:
    """Returns a deterministic echo of the last user message.

    Usage estimate: ``len(prompt) // 4`` for prompt tokens (the classic
    four-chars-per-token heuristic) and exact completion length for output
    tokens. Good enough for budget accounting in tests; never for billing.
    """

    def __init__(self, model_name: str = "echo-1") -> None:
        self._model = model_name

    @property
    def name(self) -> str:
        """Registry name: 'echo'."""
        return "echo"

    @property
    def model(self) -> str:
        """The model identifier served by this provider."""
        return self._model

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Echo the final user message; never fails, never hits network."""
        user_messages = [m for m in request.messages if m.role.value == "user"]
        last_user = user_messages[-1].content if user_messages else ""
        content = f"echo: {last_user}"
        prompt_text = "".join(m.content for m in request.messages)
        usage = Usage(
            prompt_tokens=max(1, len(prompt_text) // 4),
            completion_tokens=max(1, len(content) // 4),
        )
        return CompletionResponse(
            content=content,
            model=request.model,
            usage=usage,
            provider=self.name,
        )
