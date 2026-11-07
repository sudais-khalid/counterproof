"""Running an LLM role under the strict contract of PRD section 9.

redact -> cache lookup -> budget reservation -> call -> JSON + schema validation; on a
validation failure one retry with the error appended; a second failure drops the role's
output. Nothing here raises into the run: every failure is recorded and returns ``None``.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from importlib import resources
from typing import Final, TypeVar

from pydantic import BaseModel, ValidationError

from counterproof.llm.budget import BudgetExceededError, BudgetGuard
from counterproof.llm.cache import CachedResponse, LLMCache, cache_key
from counterproof.llm.prices import price_for
from counterproof.llm.provider import LLMRequest, Provider, ProviderError
from counterproof.llm.redact import redact
from counterproof.log import get_logger

log = get_logger("llm")
S = TypeVar("S", bound=BaseModel)
# Current prompt version per role; older files stay in prompts/ for history and replay.
PROMPT_VERSIONS: Final[dict[str, str]] = {
    "mutant_forge": "v2",
    "equivalence_judge": "v1",
    "oracle_writer": "v1",
    "triage_judge": "v2",
}
_PLACEHOLDER: Final = re.compile(r"\{\{(\w+)\}\}")
_FENCE: Final = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


@dataclass(frozen=True)
class Prompt:
    role: str
    version: str
    system: str
    user: str

    def render(self, variables: dict[str, str]) -> tuple[str, str]:
        def fill(text: str) -> str:
            return _PLACEHOLDER.sub(lambda m: variables.get(m.group(1), m.group(0)), text)

        return fill(self.system), fill(self.user)


def load_prompt(role: str, version: str | None = None) -> Prompt:
    version = version or PROMPT_VERSIONS[role]
    text = (
        resources.files("counterproof.llm")
        .joinpath(f"prompts/{role}.{version}.md")
        .read_text(encoding="utf-8")
    )
    _, _, rest = text.partition("## system\n")
    system, _, user = rest.partition("## user\n")
    return Prompt(
        role=role, version=version, system=system.strip() + "\n", user=user.strip() + "\n"
    )


@dataclass
class RoleCall:
    role: str
    model: str
    ok: bool
    cached: bool = False
    attempts: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0
    redactions: int = 0
    error: str = ""


@dataclass
class LLMSession:
    """Everything one run needs to call models: provider, cache, budget and a call log."""

    provider: Provider
    cache: LLMCache
    budget: BudgetGuard
    prices: dict[str, tuple[float, float]]
    calls: list[RoleCall] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _log(self, call: RoleCall) -> None:
        with self._lock:
            self.calls.append(call)

    def run(
        self,
        role: str,
        schema: type[S],
        variables: dict[str, str],
        *,
        model: str,
        max_tokens: int = 4096,
    ) -> S | None:
        prompt = load_prompt(role)
        system, user = prompt.render(variables)
        system, n_sys = redact(system)
        user, n_user = redact(user)
        call = RoleCall(role=role, model=model, ok=False, redactions=n_sys + n_user)
        key = cache_key(
            role=role,
            prompt_version=prompt.version,
            provider=getattr(self.provider, "cache_label", self.provider.name),
            model=model,
            system=system,
            user=user,
        )
        cached = self.cache.get(key)
        if cached is not None:
            parsed = _parse(cached.text, schema)
            if not isinstance(parsed, str):
                call.ok, call.cached = True, True
                self._log(call)
                return parsed
        if self.cache.mode == "replay":
            call.error = "cassette miss: no recorded response for this prompt"
            log.error("%s: %s (key %s)", role, call.error, key[:12])
            self._log(call)
            return None

        price = price_for(model, self.provider.name, self.prices)
        message = user
        for attempt in (1, 2):
            call.attempts = attempt
            try:
                self.budget.reserve(system + message, max_tokens, price)
            except BudgetExceededError as exc:
                call.error = str(exc)
                self.budget.note_skipped(f"{role}: {exc}")
                log.warning("%s skipped: %s", role, exc)
                break
            try:
                response = self.provider.complete(
                    LLMRequest(
                        role=role, model=model, system=system, user=message, max_tokens=max_tokens
                    )
                )
            except ProviderError as exc:
                call.error = str(exc)
                log.warning("%s failed: %s", role, exc)
                break
            call.tokens_in += response.tokens_in
            call.tokens_out += response.tokens_out
            call.usd += self.budget.record(response.tokens_in, response.tokens_out, price)
            parsed = _parse(response.text, schema)
            if not isinstance(parsed, str):
                call.ok = True
                self.cache.put(
                    key,
                    request={
                        "role": role,
                        "prompt_version": prompt.version,
                        "provider": self.provider.name,
                        "system": system,
                        "user": user,
                    },
                    response=CachedResponse(
                        response.text, call.tokens_in, call.tokens_out, response.model
                    ),
                )
                self._log(call)
                return parsed
            call.error = parsed
            log.info("%s: invalid reply (attempt %d): %s", role, attempt, parsed)
            # One retry with the validation error appended (PRD section 9).
            message = (
                f"{user}\nYour previous reply was not valid: {parsed}\n"
                "Reply again with only a json object that matches the required shape."
            )
        self._log(call)
        return None


def _parse(text: str, schema: type[S]) -> S | str:
    """The validated model, or an error message for the retry prompt."""
    stripped = _FENCE.sub("", text.strip())
    if not stripped:
        return "the reply was empty"
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return f"not valid json ({exc.msg} at position {exc.pos})"
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "<root>"
        return f"schema error at {where}: {first['msg']}"
