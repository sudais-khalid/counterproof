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


# FR-BO-07: other models each provider can write oracle tests with, in order of preference.
ORACLE_ALTERNATES: Final[dict[str, tuple[str, ...]]] = {
    "anthropic": ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"),
    "deepseek": ("deepseek-v4-pro", "deepseek-flash"),
    "ollama": ("qwen2.5-coder:7b",),
}
_FAMILIES: Final = (
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("chatgpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("codex", "openai"),
    ("deepseek", "deepseek"),
    ("qwen", "qwen"),
    ("gemini", "google"),
    ("llama", "meta"),
    ("mistral", "mistral"),
    ("codestral", "mistral"),
)


def model_family(model: str) -> str:
    name = model.lower().rsplit("/", 1)[-1]
    for prefix, family in _FAMILIES:
        if name.startswith(prefix):
            return family
    return name.split("-", 1)[0].split(":", 1)[0]


def choose_oracle_models(
    provider: str, configured: str, author_model: str | None, count: int
) -> tuple[list[str], str]:
    """FR-BO-07: ``(models, note)``, preferring another family, then another model, than
    the one that wrote the code (when the transcript names it)."""
    if configured != "auto":
        models = [configured]
        note = f"oracle model {configured} (configured)"
        if author_model and configured == author_model:
            note += f"; it is also the code author's model ({author_model})"
        return models[:count], note
    candidates = list(
        dict.fromkeys(
            [resolve_model(provider, "oracle", "auto"), *ORACLE_ALTERNATES.get(provider, ())]
        )
    )
    if not author_model:
        return candidates[:count], f"oracle model {', '.join(candidates[:count])} (author unknown)"
    family = model_family(author_model)

    def rank(model: str) -> int:
        if model_family(model) != family:
            return 0
        return 1 if model != author_model else 2

    ranked = sorted(candidates, key=rank)  # stable: keeps the preference order within a rank
    chosen = ranked[:count]
    how = {0: "different family", 1: "same family, different model", 2: "same model"}
    return chosen, (
        f"oracle model {', '.join(chosen)} (code author {author_model}: {how[rank(chosen[0])]})"
    )
