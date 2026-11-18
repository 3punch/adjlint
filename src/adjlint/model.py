"""Core data types for :mod:`adjlint`.

Standard library only. Prices are :class:`decimal.Decimal`, never ``float``:
adjustment factors are multiplied across years of history, and binary floating
point drift is exactly the class of error a data-quality gate must not import
into its own arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

__all__ = [
    "ActionKind",
    "Bar",
    "CorporateAction",
    "Finding",
    "Severity",
]


class Severity(str, Enum):
    """How much a finding should hurt. Ordering is meaningful."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def rank(self) -> int:  # pragma: no cover - trivial
        return {"info": 0, "warning": 1, "error": 2}[self.value]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank >= other.rank

    def at_least(self, threshold: Severity | None) -> bool:
        """True when this severity is at or above ``threshold`` (None = always)."""
        return True if threshold is None else self >= threshold


class ActionKind(str, Enum):
    """Corporate action types, grouped by how they move the reference price."""

    SPLIT = "split"            # 1 share becomes ``split_ratio`` shares
    BONUS = "bonus"            # capital increase from retained earnings / agio
    DIVIDEND = "dividend"      # cash paid per share
    RIGHTS = "rights"          # new shares at a subscription price
    MIXED = "mixed"            # any combination of the above in one event


@dataclass(frozen=True, slots=True)
class Bar:
    """One OHLCV observation. ``date`` is a trading *day*, not a timestamp."""

    date: date
    close: Decimal
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    volume: Decimal | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.close, Decimal):
            raise TypeError(f"Bar.close must be Decimal, got {type(self.close).__name__}")

    @property
    def has_ohlc(self) -> bool:
        return self.open is not None and self.high is not None and self.low is not None


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """A corporate action, priced as of its *effective date*.

    The effective date is the first trading day whose prices are already
    adjusted, i.e. the TSE "base price" day.  All economic parameters are
    expressed per share held *before* the event, which keeps one formula for
    every action type::

        reference = (previous_close - cash + subscription_ratio * subscription_price)
                    / (1 + bonus_ratio + (split_ratio - 1) + subscription_ratio)
    """

    symbol: str
    kind: ActionKind
    effective_date: date
    split_ratio: Decimal | None = None
    bonus_ratio: Decimal | None = None
    cash_per_share: Decimal | None = None
    subscription_price: Decimal | None = None
    subscription_ratio: Decimal | None = None
    # Closing price of the session before ``effective_date``.  Supply it when the
    # price series starts on or after the ex-date; otherwise the reference price
    # is unavailable and the action is labelled AL003 rather than guessed.
    previous_close: Decimal | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActionKind):
            object.__setattr__(self, "kind", ActionKind(self.kind))
        bare = self.split_ratio is None and self.bonus_ratio is None and self.cash_per_share is None
        rights_incomplete = self.subscription_price is None or self.subscription_ratio is None
        if bare and rights_incomplete:
            raise ValueError(f"{self.symbol} {self.effective_date}: action carries no economic terms")

    @property
    def share_multiplier(self) -> Decimal:
        """Total shares held after the event, per share held before it."""
        m = Decimal(1)
        if self.split_ratio is not None:
            m *= self.split_ratio
        if self.bonus_ratio is not None:
            m += self.bonus_ratio
        if self.subscription_ratio is not None:
            m += self.subscription_ratio
        return m

    @property
    def cash_out(self) -> Decimal:
        return self.cash_per_share if self.cash_per_share is not None else Decimal(0)

    @property
    def cash_in(self) -> Decimal:
        if self.subscription_price is None or self.subscription_ratio is None:
            return Decimal(0)
        return self.subscription_price * self.subscription_ratio

    @property
    def is_share_adjusting(self) -> bool:
        return self.share_multiplier != 1


@dataclass(frozen=True, slots=True)
class Finding:
    """A single lint finding, comparable and stable-sortable."""

    code: str
    rule: str
    severity: Severity
    message: str
    symbol: str | None = None
    on: date | None = None
    details: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> tuple:
        return (self.symbol or "", self.on or date.min, self.code)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Finding):
            return NotImplemented
        return self.key < other.key

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "rule": self.rule,
            "severity": self.severity.value,
            "message": self.message,
            "symbol": self.symbol,
            "date": self.on.isoformat() if self.on else None,
            "details": dict(self.details),
        }
