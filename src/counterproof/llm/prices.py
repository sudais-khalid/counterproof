"""Local, user-editable price table (USD per million tokens) for BudgetGuard.

Defaults were checked on 2026-11-06 against the providers' published prices; DeepSeek uses its
*peak* rates so the budget errs on the safe side. Override or extend in
``<user cache>/prices.toml``::

    [models."deepseek-v4-pro"]
    input = 1.32
    output = 3.96
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

from counterproof.evidence.store import cache_root
from counterproof.log import get_logger

log = get_logger("llm.prices")

DEFAULT_PRICES: Final[dict[str, tuple[float, float]]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "deepseek-v4-pro": (1.32, 3.96),
    "deepseek-flash": (0.30, 1.20),
}
FREE_PREFIXES: Final = ("qwen", "llama", "codellama", "deepseek-coder", "mistral", "phi")
UNKNOWN_PRICE: Final = (15.00, 75.00)
"""Deliberately high so an unpriced model cannot silently overspend the budget."""


def load_prices(path: Path | None = None) -> dict[str, tuple[float, float]]:
    prices = dict(DEFAULT_PRICES)
    path = path if path is not None else cache_root() / "prices.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return prices
    for model, entry in (data.get("models") or {}).items():
        if isinstance(entry, dict) and "input" in entry and "output" in entry:
            prices[str(model)] = (float(entry["input"]), float(entry["output"]))
    return prices


def price_for(
    model: str, provider: str, prices: dict[str, tuple[float, float]]
) -> tuple[float, float]:
    if model in prices:
        return prices[model]
    if provider == "ollama" or model.startswith(FREE_PREFIXES):
        return (0.0, 0.0)  # local models cost nothing per token
    log.warning("no price for model %s; budgeting it at %s/%s USD per 1M", model, *UNKNOWN_PRICE)
    return UNKNOWN_PRICE


def cost_usd(tokens_in: int, tokens_out: int, price: tuple[float, float]) -> float:
    return (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000
