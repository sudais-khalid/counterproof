"""Money values with exact decimal arithmetic, always in whole cents."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

CENT = Decimal("0.01")


class CurrencyMismatchError(ValueError):
    """Raised when amounts in different currencies are combined."""


@dataclass(frozen=True, order=True)
class Money:
    amount: Decimal
    currency: str = "USD"

    @classmethod
    def of(cls, value: str | int | Decimal, currency: str = "USD") -> Money:
        """Build a value rounded to whole cents with banker's rounding."""
        return cls(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_EVEN), currency)

    @classmethod
    def zero(cls, currency: str = "USD") -> Money:
        return cls.of(0, currency)

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(f"{self.currency} != {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def times(self, factor: Decimal | int | str) -> Money:
        """Multiply by ``factor`` and round the result to cents (banker's rounding)."""
        return Money.of(self.amount * Decimal(str(factor)), self.currency)

    def allocate(self, ratios: list[int]) -> list[Money]:
        """Split this amount in proportion to ``ratios``.

        The parts always add up to the original amount: leftover cents are handed out one
        at a time to the earliest parts with a non-zero ratio.
        """
        if not ratios or any(r < 0 for r in ratios) or sum(ratios) == 0:
            raise ValueError("ratios must be non-negative and not all zero")
        total = sum(ratios)
        cents = int(self.amount / CENT)
        shares = [cents * ratio // total for ratio in ratios]
        leftover = cents - sum(shares)
        for index in [i for i, ratio in enumerate(ratios) if ratio > 0][:leftover]:
            shares[index] += 1
        return [Money(share * CENT, self.currency) for share in shares]
