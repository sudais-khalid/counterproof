"""Provider interface (PRD section 9): one completion call, text in, text out.

Providers know nothing about roles, schemas, budgets or caching; those live in
:mod:`counterproof.llm.roles`. Model ids are configuration, never code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol

ProviderName = Literal["anthropic", "openai", "deepseek", "ollama", "none"]

# "auto" model ids resolve per provider (role -> model).
DEFAULT_MODELS: Final[dict[str, dict[str, str]]] = {
    "anthropic": {
        "mutant": "claude-sonnet-5-5",
        "judge": "claude-haiku-5-5",
        "oracle": "claude-sonnet-5-5",
    },
    "deepseek": {
        "mutant": "deepseek-v4-pro",
        "judge": "deepseek-flash",
        "oracle": "deepseek-v4-pro",
    },
    "ollama": {
        "mutant": "qwen2.5-coder:7b",
        "judge": "qwen2.5-coder:7b",
        "oracle": "qwen2.5-coder:7b",
    },
}


@dataclass(frozen=True)
class LLMRequest:
    role: str
    model: str
    system: str
    user: str
    max_tokens: int = 4096
    json_mode: bool = True


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


class ProviderError(Exception):
    """A call failed. ``retryable`` failures were already retried by the SDK."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class Provider(Protocol):
    name: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


class ModelNotConfiguredError(ValueError):
    """``auto`` was used for a provider without known default models (for example OpenAI)."""


def resolve_model(provider: str, role_kind: str, configured: str) -> str:
    """``auto`` becomes the provider's default for ``mutant``/``judge``/``oracle``."""
    if configured != "auto":
        return configured
    defaults = DEFAULT_MODELS.get(provider)
    if defaults is None:
        raise ModelNotConfiguredError(
            f"llm.{role_kind}_model is 'auto' but provider {provider!r} has no default models; "
            "set llm.mutant_model and llm.judge_model in counterproof.toml"
        )
    return defaults[role_kind]
