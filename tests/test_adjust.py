"""Reference-price reconstruction and adjustment-factor chaining."""

from decimal import Decimal

import pytest
from conftest import WEEK, bar

from adjlint import ActionKind, CorporateAction, adjusted_series, adjustment_factor, reference_price, resolve_actions


CLOSE = Decimal("1000")


def act(kind, day_index, **terms):
    terms = {k: Decimal(str(v)) for k, v in terms.items() if v is not None}
    return CorporateAction(symbol="TST", kind=kind, effective_date=WEEK[day_index], **terms)


def test_bonus_50_resets_the_base_price():
    action = act(ActionKind.BONUS, 1, bonus_ratio="0.5")
    assert adjustment_factor(CLOSE, action) == Decimal("0.6666666667")


def test_cash_dividend_reduces_the_base_price():
    action = act(ActionKind.DIVIDEND, 1, cash_per_share="50")
    assert reference_price(CLOSE, action) == Decimal("950")
    assert adjustment_factor(CLOSE, action) == Decimal("0.95")


def test_split_scales_the_base_price():
    action = act(ActionKind.SPLIT, 1, split_ratio="2")
    assert adjustment_factor(CLOSE, action) == Decimal("0.5")


def test_rights_issue_blends_in_the_subscription_price():
    action = act(ActionKind.RIGHTS, 1, subscription_price="400", subscription_ratio="0.25")
    assert reference_price(CLOSE, action) == Decimal("880")
    assert adjustment_factor(CLOSE, action) == Decimal("0.88")


def test_bonus_and_dividend_compose_in_one_step():
    action = act(ActionKind.MIXED, 1, bonus_ratio="0.5", cash_per_share="100")
    assert adjustment_factor(CLOSE, action) == Decimal("0.6")


def test_a_dividend_at_or_above_the_close_is_rejected_not_silently_negative():
    action = act(ActionKind.DIVIDEND, 1, cash_per_share="1000")
    with pytest.raises(ValueError, match="non-positive"):
        reference_price(CLOSE, action)


def test_resolve_prefers_the_series_close_over_a_declared_base():
    bars = [bar(0, 1000), bar(1, 500)]
    action = act(ActionKind.BONUS, 1, bonus_ratio="1", previous_close="999")
    (resolved,) = resolve_actions(bars, [action])
    assert resolved.previous_close == Decimal("1000")
    assert resolved.source == "series"


def test_resolve_falls_back_to_a_declared_base_when_the_series_starts_late():
    bars = [bar(1, 500)]
    action = act(ActionKind.BONUS, 1, bonus_ratio="1", previous_close="1000")
    (resolved,) = resolve_actions(bars, [action])
    assert resolved.factor == Decimal("0.5")
    assert resolved.source == "declared"


def test_resolve_labels_a_gap_instead_of_inventing_a_price():
    bars = [bar(1, 500)]
    action = act(ActionKind.BONUS, 0, bonus_ratio="1")
    (resolved,) = resolve_actions(bars, [action])
    assert resolved.missing_reference
    assert resolved.factor is None


def test_factors_compound_over_every_later_event():
    bars = [bar(i, c) for i, c in enumerate((1000, 900, 600))]
    actions = [
        act(ActionKind.DIVIDEND, 1, cash_per_share="100"),  # 0.9
        act(ActionKind.BONUS, 2, bonus_ratio="0.5"),        # 1/1.5
    ]
    rows = adjusted_series(bars, resolve_actions(bars, actions))
    assert [row[1] for row in rows] == [Decimal("0.6"), Decimal("0.6666666667"), Decimal("1")]
    # A correctly adjusted series is continuous across both events.
    assert rows[0][2] == Decimal("600")
    assert abs(rows[1][2] - Decimal("600")) < Decimal("0.000001")
    assert rows[2][2] == Decimal("600")


def test_an_action_without_economic_terms_is_rejected_at_construction():
    with pytest.raises(ValueError, match="no economic terms"):
        CorporateAction(symbol="TST", kind=ActionKind.BONUS, effective_date=WEEK[0])



