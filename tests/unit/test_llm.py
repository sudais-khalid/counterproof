"""LLM layer (PRD section 9): role contract, cache/cassettes, BudgetGuard, redaction,
credentials, prices, providers and the session factory. No network: fake providers only."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from counterproof.config import build_config
from counterproof.llm.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from counterproof.llm.budget import BudgetExceededError, BudgetGuard
from counterproof.llm.cache import CASSETTE_DIR_ENV, CASSETTE_MODE_ENV, LLMCache, cache_key
from counterproof.llm.credentials import api_key, ensure_template
from counterproof.llm.factory import ReplayOnlyProvider, build_session
from counterproof.llm.openai_compat import OpenAICompatProvider
from counterproof.llm.prices import UNKNOWN_PRICE, load_prices, price_for
from counterproof.llm.provider import (
    LLMRequest,
    LLMResponse,
    ModelNotConfiguredError,
    ProviderError,
    resolve_model,
)
from counterproof.llm.redact import PLACEHOLDER, may_send, redact
from counterproof.llm.roles import PROMPT_VERSIONS, LLMSession, load_prompt
from counterproof.llm.schemas import JudgeOutput

GOOD = json.dumps({"equivalent": False, "confidence": 0.9, "distinguishing_input": "x=0"})


@dataclass
class FakeProvider:
    replies: list[str | Exception]
    name: str = "deepseek"
    requests: list[LLMRequest] = field(default_factory=list)

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(text=reply, tokens_in=1000, tokens_out=200, model=request.model)


def session(
    replies: list[str | Exception],
    tmp_path: Path,
    *,
    mode: str = "readwrite",
    budget: BudgetGuard | None = None,
) -> tuple[LLMSession, FakeProvider]:
    provider = FakeProvider(list(replies))
    s = LLMSession(
        provider=provider,
        cache=LLMCache(tmp_path / "cache", mode),  # type: ignore[arg-type]
        budget=budget or BudgetGuard(max_tokens=100_000, max_usd=1.0, max_seconds=600),
        prices={"deepseek-flash": (0.30, 1.20)},
    )
    return s, provider


VARS = {
    "original": "def f(x):\n    return x",
    "mutant": "def f(x):\n    return -x",
    "tests": "pass",
}


def run(s: LLMSession) -> JudgeOutput | None:
    return s.run("equivalence_judge", JudgeOutput, VARS, model="deepseek-flash", max_tokens=500)


def test_prompts_are_versioned_files_with_both_parts() -> None:
    for role, version in PROMPT_VERSIONS.items():
        prompt = load_prompt(role)
        assert prompt.version == version
        assert load_prompt(role, "v1").version == "v1"  # history stays loadable
        assert "json" in prompt.system.lower()  # DeepSeek JSON mode requires the word
        assert prompt.user.strip()


def test_valid_reply_is_parsed_costed_and_cached(tmp_path: Path) -> None:
    s, provider = session([GOOD], tmp_path)
    first = run(s)
    assert first is not None and first.confidence == 0.9
    assert s.calls[-1].usd == pytest.approx((1000 * 0.30 + 200 * 1.20) / 1e6)
    second = run(s)  # same inputs: served from cache, provider not called again
    assert second == first
    assert len(provider.requests) == 1
    assert s.calls[-1].cached and s.calls[-1].usd == 0


def test_invalid_reply_is_retried_once_with_the_error(tmp_path: Path) -> None:
    s, provider = session(["not json at all", "```json\n" + GOOD + "\n```"], tmp_path)
    assert run(s) is not None
    assert len(provider.requests) == 2
    assert "Your previous reply was not valid" in provider.requests[1].user
    assert s.calls[-1].attempts == 2


@pytest.mark.parametrize(
    "replies",
    [
        ["{}", '{"equivalent": "maybe"}'],
        ["   ", ""],  # DeepSeek JSON mode can return blank content
    ],
)
def test_second_invalid_reply_drops_the_output_without_raising(
    tmp_path: Path, replies: list[str]
) -> None:
    s, _ = session(list(replies), tmp_path)
    assert run(s) is None
    assert not s.calls[-1].ok and s.calls[-1].error


def test_provider_error_is_recorded_not_raised(tmp_path: Path) -> None:
    s, _ = session([ProviderError("deepseek: HTTP 500", retryable=True)], tmp_path)
    assert run(s) is None
    assert "HTTP 500" in s.calls[-1].error


def test_replay_mode_never_calls_the_provider(tmp_path: Path) -> None:
    recorder, _ = session([GOOD], tmp_path, mode="record")
    assert run(recorder) is not None
    replay, provider = session([], tmp_path, mode="replay")
    assert run(replay) is not None and replay.calls[-1].cached
    other = dict(VARS, tests="def test_other(): pass")
    assert replay.run("equivalence_judge", JudgeOutput, other, model="deepseek-flash") is None
    assert "cassette miss" in replay.calls[-1].error
    assert provider.requests == []


def test_cache_key_covers_role_version_provider_model_and_prompt() -> None:
    base = {
        "role": "r",
        "prompt_version": "v1",
        "provider": "deepseek",
        "model": "m",
        "system": "s",
        "user": "u",
    }
    keys = {cache_key(**base)}
    for field_name, value in base.items():
        keys.add(cache_key(**{**base, field_name: value + "x"}))
    assert len(keys) == 1 + len(base)


def test_budget_hard_stop_on_usd(tmp_path: Path) -> None:
    # Each call reserves its worst case (prompt + max_tokens): ~$0.0007 here, and really
    # costs $0.00054. The first fits under $0.001; the second's worst case does not.
    budget = BudgetGuard(max_tokens=10**9, max_usd=0.001, max_seconds=600)
    s, provider = session([GOOD, GOOD], tmp_path, budget=budget, mode="off")
    assert run(s) is not None
    assert run(s) is None
    assert len(provider.requests) == 1
    assert budget.skipped and "USD budget exhausted" in budget.skipped[0]
    assert budget.usd <= 0.001


def test_budget_hard_stop_on_tokens_and_time() -> None:
    tokens = BudgetGuard(max_tokens=1000, max_usd=10, max_seconds=600)
    with pytest.raises(BudgetExceededError, match="token"):
        tokens.reserve("x" * 4000, 500, (1.0, 1.0))
    now = [0.0]
    timed = BudgetGuard(max_tokens=10**9, max_usd=10, max_seconds=90, clock=lambda: now[0])
    timed.reserve("hi", 10, (1.0, 1.0))
    now[0] = 90.0
    with pytest.raises(BudgetExceededError, match="time"):
        timed.reserve("hi", 10, (1.0, 1.0))


def test_redaction_of_known_secrets_and_high_entropy_tokens() -> None:
    # Fake secrets are assembled at run time so no secret-shaped literal sits in the source
    # (GitHub push protection rightly blocks those, even in tests).
    anthropic_key = "sk-" + "ant-api03-" + "abcdefghijklmnopqrstuvwxyz0123456789"
    github_token = "gh" + "p_" + "0123456789abcdefghijABCDEFGHIJ0123456789"
    aws_key_id = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
    private_key = "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIE\n-----END RSA " + "PRIVATE KEY-----"
    text = "\n".join(
        [
            f"key = '{anthropic_key}'",
            f"token: {github_token}",
            f"aws = {aws_key_id}",
            'API_KEY = "hunter2hunter2"',
            "blob = 9fK2mQ7xZ1pL8vB3nR6tY4wE0aS5dH2jC7uI9oP1",
            private_key,
            "def percent_discount_with_a_rather_long_descriptive_name(total): pass",
        ]
    )
    redacted, count = redact(text)
    assert count >= 6
    for secret in (anthropic_key, github_token, aws_key_id, "hunter2", "9fK2mQ7x", "MIIE"):
        assert secret not in redacted
    assert "percent_discount_with_a_rather_long_descriptive_name" in redacted
    assert PLACEHOLDER in redacted


def test_never_send_secrets_lockfiles_or_excluded_paths() -> None:
    assert may_send("src/ledger/money.py", [])
    for path in (".env", "config/.env.local", "uv.lock", "deploy/key.pem", "credentials.json"):
        assert not may_send(path, [])
    assert not may_send("migrations/0001.py", ["migrations/**"])


def test_credentials_env_first_then_user_file(tmp_path: Path) -> None:
    path = tmp_path / "credentials.toml"
    assert ensure_template(path)
    assert not ensure_template(path)
    assert api_key("deepseek", env={}, path=path) is None  # empty template
    path.write_text('[deepseek]\napi_key = " sk-file "\n', encoding="utf-8")
    assert api_key("deepseek", env={}, path=path) == "sk-file"
    assert api_key("deepseek", env={"DEEPSEEK_API_KEY": "sk-env"}, path=path) == "sk-env"
    assert api_key("anthropic", env={}, path=path) is None


def test_prices_defaults_overrides_unknown_and_local(tmp_path: Path) -> None:
    override = tmp_path / "prices.toml"
    override.write_text('[models."deepseek-flash"]\ninput = 0.15\noutput = 0.6\n', encoding="utf-8")
    prices = load_prices(override)
    assert prices["deepseek-flash"] == (0.15, 0.6)
    assert prices["claude-haiku-5-5"] == (0.10, 0.50)
    assert price_for("mystery-model", "openai", prices) == UNKNOWN_PRICE
    assert price_for("qwen2.5-coder:7b", "ollama", prices) == (0.0, 0.0)


def test_auto_models_resolve_per_provider() -> None:
    assert resolve_model("deepseek", "mutant", "auto") == "deepseek-v4-pro"
    assert resolve_model("deepseek", "judge", "auto") == "deepseek-flash"
    assert resolve_model("anthropic", "judge", "auto") == "claude-haiku-5-5"
    assert resolve_model("openai", "judge", "gpt-x") == "gpt-x"
    with pytest.raises(ModelNotConfiguredError):
        resolve_model("openai", "judge", "auto")


def test_factory(tmp_path: Path) -> None:
    none = build_session(build_config())
    assert none[0] is None and "provider = none" in none[2]
    deepseek = build_config({"llm": {"provider": "deepseek"}})
    no_key = build_session(deepseek, env={})
    assert no_key[0] is None and "no API key for deepseek" in no_key[2]
    replay_env = {CASSETTE_DIR_ENV: str(tmp_path), CASSETTE_MODE_ENV: "replay"}
    replay, models, _ = build_session(deepseek, env=replay_env)
    assert replay is not None and isinstance(replay.provider, ReplayOnlyProvider)
    assert models == {"mutant": "deepseek-v4-pro", "judge": "deepseek-flash"}
    assert replay.budget.max_seconds == 300  # full profile target
    openai_auto = build_session(build_config({"llm": {"provider": "openai"}}), env=replay_env)
    assert openai_auto[0] is None and "has no default models" in openai_auto[2]


class FakeOpenAIClient:
    def __init__(self, content: str | None = '{"a": 1}') -> None:
        self.kwargs: dict[str, Any] = {}
        self.content = content
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        message = SimpleNamespace(content=self.content)
        usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage, model="m")


REQ = LLMRequest(role="r", model="deepseek-flash", system="s json", user="u", max_tokens=99)


def test_deepseek_request_shape() -> None:
    client = FakeOpenAIClient()
    response = OpenAICompatProvider("deepseek", api_key="k", client=client).complete(REQ)
    assert (response.text, response.tokens_in, response.tokens_out) == ('{"a": 1}', 11, 7)
    assert client.kwargs["response_format"] == {"type": "json_object"}
    assert client.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert client.kwargs["max_tokens"] == 99
    assert client.kwargs["messages"][0] == {"role": "system", "content": "s json"}
    thinking = FakeOpenAIClient()
    OpenAICompatProvider("deepseek", api_key="k", thinking=True, client=thinking).complete(REQ)
    assert thinking.kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in thinking.kwargs
    blank = OpenAICompatProvider("deepseek", api_key="k", client=FakeOpenAIClient(None))
    assert blank.complete(REQ).text == ""  # the role runner retries blank replies


class FakeAnthropicClient:
    def __init__(self, stop_reason: str = "end_turn") -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.stop_reason = stop_reason
        self.messages = SimpleNamespace(create=lambda **kw: self._reply("messages", kw))
        self.beta = SimpleNamespace(
            messages=SimpleNamespace(create=lambda **kw: self._reply("beta", kw))
        )

    def _reply(self, path: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append((path, kwargs))
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text="{}"),
            ],
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=5, output_tokens=3),
            model=kwargs["model"],
        )


def test_anthropic_request_shape_fallbacks_and_refusal() -> None:
    client = FakeAnthropicClient()
    provider = AnthropicProvider(api_key="k", client=client)
    haiku = provider.complete(LLMRequest(role="r", model="claude-haiku-5-5", system="s", user="u"))
    assert haiku.text == "{}" and client.calls[-1][0] == "messages"
    assert "temperature" not in client.calls[-1][1]
    provider.complete(LLMRequest(role="r", model="claude-sonnet-5-5", system="s", user="u"))
    path, kwargs = client.calls[-1]
    assert (
        path == "beta" and kwargs["betas"] == [FALLBACK_BETA] and kwargs["fallbacks"] == "default"
    )
    refusing = AnthropicProvider(api_key="k", client=FakeAnthropicClient("refusal"))
    with pytest.raises(ProviderError, match="refusal"):
        refusing.complete(LLMRequest(role="r", model="claude-haiku-5-5", system="s", user="u"))


def test_output_changing_settings_are_part_of_the_cache_key() -> None:
    plain = OpenAICompatProvider("deepseek", api_key="k", client=FakeOpenAIClient())
    thinking = OpenAICompatProvider(
        "deepseek", api_key="k", thinking=True, client=FakeOpenAIClient()
    )
    assert plain.cache_label == "deepseek"  # recorded cassettes keep their keys
    assert thinking.cache_label == "deepseek+thinking"
