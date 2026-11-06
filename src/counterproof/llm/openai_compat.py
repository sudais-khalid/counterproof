"""OpenAI-compatible chat completions: OpenAI, DeepSeek and Ollama (``/v1``) via the
official ``openai`` SDK with a different ``base_url``.

DeepSeek specifics, checked against api-docs.deepseek.com: thinking is *on* unless disabled,
so JSON roles send ``thinking: {"type": "disabled"}`` by default; JSON mode is
``response_format={"type": "json_object"}`` and may return blank content, which the role
runner treats as an invalid reply and retries once.
"""

from __future__ import annotations

from typing import Any, Final

import openai

from counterproof.llm.provider import LLMRequest, LLMResponse, ProviderError

BASE_URLS: Final[dict[str, str | None]] = {
    "openai": None,
    "deepseek": "https://api.deepseek.com",
    "ollama": "http://127.0.0.1:11434/v1",
}
REQUEST_TIMEOUT_S: Final = 180.0


class OpenAICompatProvider:
    def __init__(
        self,
        name: str,
        *,
        api_key: str | None,
        base_url: str | None = None,
        thinking: bool = False,
        client: Any = None,
    ) -> None:
        self.name = name
        self.thinking = thinking
        # Settings that change the model's output belong in the cache key.
        self.cache_label = f"{name}+thinking" if name == "deepseek" and thinking else name
        self._client = client or openai.OpenAI(
            api_key=api_key or ("ollama" if name == "ollama" else None),
            base_url=base_url or BASE_URLS.get(name),
            timeout=REQUEST_TIMEOUT_S,
            max_retries=2,  # connection errors, 429 and 5xx
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "max_tokens": request.max_tokens,
        }
        if request.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if self.name == "deepseek":
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled" if self.thinking else "disabled"}
            }
        if not (self.name == "deepseek" and self.thinking):
            kwargs["temperature"] = 0.2  # ignored by DeepSeek in thinking mode
        try:
            response = self._client.chat.completions.create(**kwargs)
        except (
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ) as exc:
            raise ProviderError(f"{self.name}: {type(exc).__name__}", retryable=True) from exc
        except openai.APIStatusError as exc:
            raise ProviderError(f"{self.name}: HTTP {exc.status_code}: {exc.message}") from exc
        except openai.OpenAIError as exc:
            raise ProviderError(f"{self.name}: {exc}") from exc
        choice = response.choices[0] if response.choices else None
        text = (choice.message.content or "") if choice is not None else ""
        usage = response.usage
        return LLMResponse(
            text=text,
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
            model=response.model or request.model,
        )
