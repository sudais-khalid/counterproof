"""BudgetGuard: hard per-run limits on LLM tokens, USD and wall time (PRD section 9).

Every call reserves its worst case (prompt estimate plus ``max_tokens``) before it is sent.
A call that could exceed any limit is not made; it is recorded as skipped so the certificate
can say exactly what was not done.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

from counterproof.llm.prices import cost_usd

CHARS_PER_TOKEN: Final = 3.5


class BudgetExceededError(Exception):
    def __init__(self, limit: str, detail: str) -> None:
        super().__init__(f"LLM {limit} budget exhausted: {detail}")
        self.limit = limit


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


@dataclass
class BudgetGuard:
    max_tokens: int
    max_usd: float
    max_seconds: float
    clock: Callable[[], float] = time.monotonic
    tokens: int = 0
    usd: float = 0.0
    skipped: list[str] = field(default_factory=list)
    _started: float = field(default=-1.0)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self._started = self.clock()

    @property
    def elapsed_s(self) -> float:
        return self.clock() - self._started

    def reserve(self, prompt: str, max_out: int, price: tuple[float, float]) -> None:
        """Raise :class:`BudgetExceededError` if the worst case of this call does not fit."""
        est_in = estimate_tokens(prompt)
        with self._lock:
            if self.elapsed_s >= self.max_seconds:
                raise BudgetExceededError(
                    "time", f"{self.elapsed_s:.0f}s used of {self.max_seconds:.0f}s"
                )
            if self.tokens + est_in + max_out > self.max_tokens:
                raise BudgetExceededError(
                    "token", f"{self.tokens} used, call needs up to {est_in + max_out}"
                )
            worst = cost_usd(est_in, max_out, price)
            if self.usd + worst > self.max_usd:
                raise BudgetExceededError(
                    "USD", f"${self.usd:.4f} used, call could cost up to ${worst:.4f}"
                )

    def record(self, tokens_in: int, tokens_out: int, price: tuple[float, float]) -> float:
        usd = cost_usd(tokens_in, tokens_out, price)
        with self._lock:
            self.tokens += tokens_in + tokens_out
            self.usd += usd
        return usd

    def note_skipped(self, what: str) -> None:
        with self._lock:
            self.skipped.append(what)
