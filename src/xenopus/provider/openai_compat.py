"""OpenAI-compatible HTTP provider adapter (chat completions).

Works with any endpoint implementing the OpenAI /chat/completions schema
(OpenAI, OpenRouter, z.ai, LM Studio, llama.cpp server, Ollama's
compatibility mode, vLLM). Hard rules:

- API keys come ONLY from explicit constructor injection (callers read
  env/secret stores); this module never reads os.environ itself.
- Timeouts are mandatory on every call (master prompt 18.4: connect AND
  read timeout) — no request may hang the runtime.
- Response schema is validated; malformed payloads raise SchemaError
  (permanent) rather than returning junk.
"""

from typing import Any

import httpx

from xenopus.provider.types import (
    AuthError,
    CompletionRequest,
    CompletionResponse,
    Message,
    ProviderUnavailableError,
    RateLimitError,
    SchemaError,
    Usage,
)

DEFAULT_TIMEOUT_SECONDS = 60.0


class OpenAICompatProvider:
    """Async provider for one OpenAI-compatible endpoint.

    Contract:
        base_url: e.g. ``https://api.openai.com/v1`` — no trailing slash
            on paths is appended here; the adapter hits ``/chat/completions``.
        api_key: injected by the caller; NEVER logged, NEVER persisted here.
        model: default model id; overridable per request.

    Failure modes:
        AuthError on 401/403; RateLimitError on 429 (with Retry-After
        honored when present); ProviderUnavailableError on 5xx and network
        errors; SchemaError on 200-with-malformed body.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        name: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            msg = f"base_url must be an http(s) URL, got {base_url!r}"
            raise ValueError(msg)
        if not api_key:
            msg = "api_key must be a non-empty string (inject from a secret store)"
            raise ValueError(msg)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._name = name
        self._timeout = httpx.Timeout(timeout_seconds)
        self._client = client

    @property
    def name(self) -> str:
        """Registry name supplied at construction."""
        return self._name

    @property
    def model(self) -> str:
        """Default model id for this endpoint."""
        return self._model

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """POST /chat/completions and validate the response schema."""
        payload = self._build_payload(request)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as err:
            msg = f"provider request failed: {err}"
            raise ProviderUnavailableError(msg) from err
        finally:
            if self._client is None:
                await client.aclose()

        if response.status_code in (401, 403):
            msg = f"auth rejected (HTTP {response.status_code})"
            raise AuthError(msg)
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                retry_after_seconds = float(retry_after) if retry_after else None
            except ValueError:
                retry_after_seconds = None
            msg = "rate limited (HTTP 429)"
            raise RateLimitError(msg, retry_after_seconds=retry_after_seconds)
        if response.status_code >= 500:
            msg = f"provider server error (HTTP {response.status_code})"
            raise ProviderUnavailableError(msg)
        if response.status_code != 200:
            msg = f"unexpected status {response.status_code}: {response.text[:200]}"
            raise SchemaError(msg)

        return self._parse_response(response.json())

    # -- internals ---------------------------------------------------------

    def _build_payload(self, request: CompletionRequest) -> dict[str, Any]:
        """Map internal request to the OpenAI chat schema."""
        messages = [
            {"role": message.role.value, "content": message.content} for message in request.messages
        ]
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature,
        }
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        return payload

    @staticmethod
    def _parse_response(data: Any) -> CompletionResponse:
        """Validate the response body and build a CompletionResponse.

        Raises SchemaError when required fields are missing or mistyped —
        a 200 with junk is a contract violation, not a success.
        """
        if not isinstance(data, dict):
            msg = "response body is not a JSON object"
            raise SchemaError(msg)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            msg = "response has no choices[]"
            raise SchemaError(msg)
        first = choices[0]
        if not isinstance(first, dict):
            msg = "choices[0] is not an object"
            raise SchemaError(msg)
        message = first.get("message")
        if not isinstance(message, dict) or "content" not in message:
            msg = "choices[0].message missing content"
            raise SchemaError(msg)
        content = message.get("content")
        if not isinstance(content, str):
            msg = "choices[0].message.content is not a string"
            raise SchemaError(msg)
        model = data.get("model")
        if not isinstance(model, str) or not model:
            msg = "response model field missing or empty"
            raise SchemaError(msg)

        usage_data = data.get("usage") or {}
        if not isinstance(usage_data, dict):
            msg = "usage field is not an object"
            raise SchemaError(msg)
        usage = Usage(
            prompt_tokens=_as_int(usage_data.get("prompt_tokens"), 0),
            completion_tokens=_as_int(usage_data.get("completion_tokens"), 0),
            total_tokens=_as_int(usage_data.get("total_tokens"), 0),
        )

        return CompletionResponse(
            content=content,
            model=model,
            usage=usage,
            provider=str(data.get("provider", "openai-compat")),
            finish_reason=str(first.get("finish_reason", "stop")),
            raw=data,
        )


def _as_int(value: Any, default: int) -> int:
    """Coerce a schema field to int; missing/malformed -> default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


def message_to_openai(message: Message) -> dict[str, str]:
    """Convert an internal Message to the wire schema (test seam)."""
    return {"role": message.role.value, "content": message.content}
