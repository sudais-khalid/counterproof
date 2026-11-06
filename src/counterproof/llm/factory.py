"""Build the per-run :class:`LLMSession` from configuration (or explain why there is none)."""

from __future__ import annotations

from collections.abc import Mapping

from counterproof.config import Config
from counterproof.llm.anthropic_provider import AnthropicProvider
from counterproof.llm.budget import BudgetGuard
from counterproof.llm.cache import LLMCache
from counterproof.llm.credentials import ENV_VARS, api_key, credentials_path
from counterproof.llm.openai_compat import OpenAICompatProvider
from counterproof.llm.prices import load_prices
from counterproof.llm.provider import (
    LLMRequest,
    LLMResponse,
    ModelNotConfiguredError,
    Provider,
    ProviderError,
    resolve_model,
)
from counterproof.llm.roles import LLMSession


class ReplayOnlyProvider:
    """Cassette replay: the network is never touched; a cache miss is reported instead."""

    def __init__(self, name: str) -> None:
        self.name = name

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise ProviderError(f"{self.name}: replay mode never calls the provider")


def build_session(
    config: Config, *, env: Mapping[str, str] | None = None
) -> tuple[LLMSession | None, dict[str, str], str]:
    """``(session, models_by_role, note)``; ``session`` is None when no LLM is usable."""
    name = config.llm.provider
    if name == "none":
        return None, {}, "LLM disabled (provider = none): rule mutants only"
    try:
        models = {
            "mutant": resolve_model(name, "mutant", config.llm.mutant_model),
            "judge": resolve_model(name, "judge", config.llm.judge_model),
        }
    except ModelNotConfiguredError as exc:
        return None, {}, f"{exc}; rule mutants only"
    cache = LLMCache.from_env(dict(env) if env is not None else None)
    provider: Provider
    if cache.mode == "replay":
        provider = ReplayOnlyProvider(name)
    else:
        key = api_key(name, env=env)
        if key is None and name in {"deepseek", "openai"}:
            return (
                None,
                {},
                f"no API key for {name} (set {ENV_VARS[name]} or add it to "
                f"{credentials_path()}): rule mutants only",
            )
        if name == "anthropic":
            provider = AnthropicProvider(api_key=key)
        else:
            provider = OpenAICompatProvider(
                name, api_key=key, base_url=config.llm.base_url, thinking=config.llm.thinking
            )
    seconds = config.llm.budget_seconds or float(config.profile_spec.target_seconds)
    budget = BudgetGuard(
        max_tokens=config.llm.budget_tokens, max_usd=config.llm.budget_usd, max_seconds=seconds
    )
    session = LLMSession(provider=provider, cache=cache, budget=budget, prices=load_prices())
    return session, models, f"provider {name} ({models['mutant']}, {models['judge']})"
