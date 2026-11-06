"""Anthropic Messages API via the official ``anthropic`` SDK.

Current Claude models reject ``temperature`` and run adaptive thinking by default, so
neither is sent. For Sonnet/Opus the server-side refusal fallback (``fallbacks="default"``,
beta ``server-side-fallback-2026-07-01``) is enabled; Haiku has no server-side fallback. A
final ``stop_reason == "refusal"`` is a failed call, which the role runner skips gracefully.
"""

from __future__ import annotations

from typing import Any, Final

import anthropic

from counterproof.llm.provider import LLMRequest, LLMResponse, ProviderError

FALLBACK_BETA: Final = "server-side-fallback-2026-07-01"
FALLBACK_MODELS: Final = (
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-fable-5-1",
)
REQUEST_TIMEOUT_S: Final = 180.0


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, api_key: str | None, client: Any = None) -> None:
        # With no key the SDK resolves ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile.
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=REQUEST_TIMEOUT_S, max_retries=2
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
        }
        try:
            if request.model in FALLBACK_MODELS:
                message = self._client.beta.messages.create(
                    **kwargs, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                message = self._client.messages.create(**kwargs)
        except (
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        ) as exc:
            raise ProviderError(f"anthropic: {type(exc).__name__}", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"anthropic: HTTP {exc.status_code}: {exc.message}") from exc
        except anthropic.AnthropicError as exc:
            raise ProviderError(f"anthropic: {exc}") from exc
        if message.stop_reason == "refusal":
            raise ProviderError("anthropic: the model declined the request (refusal)")
        text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
        return LLMResponse(
            text=text,
            tokens_in=message.usage.input_tokens,
            tokens_out=message.usage.output_tokens,
            model=message.model,
        )
