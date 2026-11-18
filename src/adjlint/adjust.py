"""Reference-price reconstruction and adjustment-factor chains.

One formula covers every action type because :class:`~adjlint.model.CorporateAction`
normalises its economic terms to "per share held before the event":

    reference = (previous_close - cash + subscription_ratio * subscription_price)
                / share_multiplier
    factor    = reference / previous_close

Prices older than the effective date are multiplied by the cumulative product of
the factors that follow them (classic backward adjustment), which keeps returns
continuous across the event in *current* share terms.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .model import Bar, CorporateAction

__all__ = [
    "ResolvedAction",
    "adjusted_series",
    "adjustment_factor",
    "cumulative_factor",
    "reference_price",
    "resolve_actions",
]

#: Adjustment factors are irrational in general (1/3, ...) and are compounded
#: across decades.  Fixing the working precision keeps runs byte-reproducible.
FACTOR_DECIMALS = 10
_QUANT = Decimal(1).scaleb(-FACTOR_DECIMALS)

ONE = Decimal(1)


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_QUANT)


@dataclass(frozen=True, slots=True)
class ResolvedAction:
    """A corporate action tied to the price series it should be visible in."""

    action: CorporateAction
    previous_close: Decimal | None
    reference: Decimal | None
    factor: Decimal | None
    #: ``"series"`` | ``"declared"`` | ``None`` (unresolvable -> AL003)
    source: str | None = None

    @property
    def usable(self) -> bool:
        return self.factor is not None

    @property
    def missing_reference(self) -> bool:
        return self.factor is None


def reference_price(previous_close: Decimal, action: CorporateAction) -> Decimal:
    """Theoretical opening level on the effective date ("base price")."""
    if previous_close <= 0:
        raise ValueError(f"previous_close must be positive, got {previous_close}")
    numerator = previous_close - action.cash_out + action.cash_in
    denominator = action.share_multiplier
    if denominator <= 0:
        raise ValueError(f"{action.symbol} {action.effective_date}: non-positive share multiplier")
    if numerator <= 0:
        # A dividend at or above the previous close cannot be a real event;
        # surfacing it as a crash here beats silently producing a negative factor.
        raise ValueError(
            f"{action.symbol} {action.effective_date}: reference price would be "
            f"non-positive ({numerator}); check cash_per_share"
        )
    return numerator / denominator


def adjustment_factor(previous_close: Decimal, action: CorporateAction) -> Decimal:
    return _quantize(reference_price(previous_close, action) / previous_close)


def resolve_actions(
    bars: list[Bar] | tuple[Bar, ...],
    actions: list[CorporateAction] | tuple[CorporateAction, ...],
) -> list[ResolvedAction]:
    """Attach a previous close / reference price / factor to every action.

    The closing bar immediately *before* the effective date wins over a declared
    ``previous_close``; a disagreement between the two is a finding for the rule
    layer (wrong ex-date is one of the most common vendor-data bugs).
    """
    ordered = sorted(bars, key=lambda b: b.date)
    dates = [b.date for b in ordered]
    out: list[ResolvedAction] = []
    for action in sorted(actions, key=lambda a: a.effective_date):
        idx = _bisect_left_date(dates, action.effective_date) - 1
        from_series = ordered[idx].close if idx >= 0 else None
        previous = from_series if from_series is not None else action.previous_close
        source = "series" if from_series is not None else (
            "declared" if action.previous_close is not None else None
        )
        if previous is None or previous <= 0:
            out.append(ResolvedAction(action, from_series, None, None, source))
            continue
        try:
            ref = reference_price(previous, action)
            factor = _quantize(ref / previous)
        except ValueError:
            out.append(ResolvedAction(action, previous, None, None, source))
            continue
        out.append(ResolvedAction(action, previous, ref, factor, source))
    return out


def _bisect_left_date(sorted_dates: list[date], target: date) -> int:
    lo, hi = 0, len(sorted_dates)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_dates[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def cumulative_factor(resolved: list[ResolvedAction], on: date) -> Decimal:
    """Product of every adjustment factor strictly after ``on``."""
    factor = ONE
    for item in resolved:
        if item.factor is not None and item.action.effective_date > on:
            factor *= item.factor
    return _quantize(factor)


def adjusted_series(
    bars: list[Bar] | tuple[Bar, ...],
    resolved: list[ResolvedAction],
) -> list[tuple[Bar, Decimal, Decimal]]:
    """``(bar, cumulative_factor, adjusted_close)`` ordered by date."""
    out = []
    for bar in sorted(bars, key=lambda b: b.date):
        factor = cumulative_factor(resolved, bar.date)
        out.append((bar, factor, _quantize(bar.close * factor)))
    return out
