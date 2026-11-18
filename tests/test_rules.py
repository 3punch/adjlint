"""One positive and one negative case per rule, plus the config switches."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from adjlint import (
    ActionKind,
    AuditConfig,
    CorporateAction,
    MarketProfile,
    ProfileError,
    Severity,
    SymbolSeries,
    audit,
)
from conftest import WEEK, at, bar, by_code, codes

FLAT_5 = MarketProfile(name="test", weekend_days=(5, 6), default_band=Decimal("0.05"))


def run(bars, actions=(), vendor=None, config=None, calendar=None, profile=None):
    series = SymbolSeries.of("TST", bars, tuple(actions), vendor)
    return audit(
        series,
        profile=profile or FLAT_5,
        calendar=calendar,
        config=config or AuditConfig(),
    )


def act(kind, index=None, day=None, **terms):
    terms = {k: Decimal(str(v)) for k, v in terms.items() if v is not None}
    return CorporateAction(
        symbol="TST", kind=kind, effective_date=day or WEEK[index], **terms
    )


def sessions(count, start=date(2026, 3, 2)):
    """``count`` Mon-Fri session days starting on a Monday."""
    out, cursor = [], start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


# --------------------------------------------------------------------------- #
# AL001 band-limit-breach
# --------------------------------------------------------------------------- #


def test_a_20_percent_jump_with_nothing_booked_is_an_error():
    findings = run([bar(0, 1000), bar(1, 1200)])
    assert codes(findings) == ["AL001"]
    assert findings[0].severity is Severity.ERROR
    assert findings[0].details["observed"] == "20.00%"


def test_a_quiet_series_raises_nothing():
    assert run([bar(i, 1000 + 5 * i) for i in range(5)]) == []


def test_a_booked_capital_increase_explains_the_reset():
    findings = run([bar(0, 1000), bar(1, 665)], [act(ActionKind.BONUS, 1, bonus_ratio="0.5")])
    assert findings == []


# --------------------------------------------------------------------------- #
# AL002 action-not-applied
# --------------------------------------------------------------------------- #


def test_a_booked_dividend_that_never_moves_the_price_is_an_error():
    findings = run([bar(0, 1000), bar(1, 1000)], [act(ActionKind.DIVIDEND, 1, cash_per_share="20")])
    assert codes(findings) == ["AL002"]
    assert findings[0].details["expected"] == "-2.00%"


def test_a_dividend_the_feed_did_apply_stays_quiet():
    findings = run([bar(0, 1000), bar(1, 980)], [act(ActionKind.DIVIDEND, 1, cash_per_share="20")])
    assert findings == []


# --------------------------------------------------------------------------- #
# AL003 unclassified-gap
# --------------------------------------------------------------------------- #


def test_an_event_before_the_series_is_labelled_not_imputed():
    findings = run([bar(0, 1000), bar(1, 990)], [act(ActionKind.DIVIDEND, 0, cash_per_share="20")])
    assert codes(findings) == ["AL003"]
    assert findings[0].severity is Severity.WARNING
    assert "previous_close" in findings[0].details["fix"]


def test_supplying_the_base_price_closes_the_gap():
    action = act(ActionKind.DIVIDEND, 0, cash_per_share="20", previous_close="1020")
    assert run([bar(0, 1000), bar(1, 990)], [action]) == []


# --------------------------------------------------------------------------- #
# AL004 duplicate-event
# --------------------------------------------------------------------------- #


def test_the_same_event_booked_twice_is_an_error():
    doubled = [act(ActionKind.BONUS, 1, bonus_ratio="0.5")] * 2
    findings = run([bar(0, 1000), bar(1, 665)], doubled)
    assert "AL004" in codes(findings)
    assert at(findings, WEEK[1])[0].severity is Severity.ERROR


def test_two_different_events_on_one_day_only_warn():
    pair = [
        act(ActionKind.BONUS, 1, bonus_ratio="0.5"),
        act(ActionKind.DIVIDEND, 1, cash_per_share="5"),
    ]
    findings = [f for f in run([bar(0, 1000), bar(1, 665)], pair) if f.code == "AL004"]
    assert [f.severity for f in findings] == [Severity.WARNING]


# --------------------------------------------------------------------------- #
# AL005 outlier-return
# --------------------------------------------------------------------------- #


def test_a_robust_outlier_survives_a_long_quiet_series():
    days = sessions(26)
    closes = [Decimal(1000 + i) for i in range(26)]
    closes[-1] = Decimal("1523")
    rows = [bar(0, c, day=d) for d, c in zip(days, closes, strict=False)]
    findings = run(rows)
    assert "AL005" in codes(findings)
    assert at(findings, days[-1])[1 - 1].details  # AL001 and AL005 both land on that day


def test_short_series_are_never_called_outliers():
    rows = [bar(i, 1000 + i) for i in range(6)] + [bar(6, 900, day=WEEK[6])]
    assert "AL005" not in codes(run(rows))


# --------------------------------------------------------------------------- #
# AL006 calendar-misalignment
# --------------------------------------------------------------------------- #


def test_bars_arriving_out_of_order_are_an_error():
    findings = by_code(run([bar(1, 1000), bar(0, 1005), bar(2, 1010)]), "AL006")
    assert any("out of order" in f.message for f in findings)


def test_a_duplicated_session_is_an_error():
    findings = by_code(run([bar(0, 1000), bar(0, 1002)]), "AL006")
    assert [f.severity for f in findings] == [Severity.ERROR]


def test_a_bar_on_a_weekend_is_an_error():
    saturday = date(2026, 3, 7)
    findings = by_code(run([bar(0, 1000), bar(None, 1005, day=saturday)]), "AL006")
    assert any("non-session" in f.message for f in findings)


def test_a_missing_session_is_only_reported_against_an_explicit_calendar():
    from adjlint import TradingCalendar

    rows = [bar(0, 1000), bar(2, 1005)]  # WEEK[1] absent
    assert by_code(run(rows), "AL006") == []
    calendar = TradingCalendar.from_dates([WEEK[0], WEEK[1], WEEK[2]])
    gaps = by_code(run(rows, calendar=calendar), "AL006")
    assert [f.on for f in gaps] == [WEEK[1]]


# --------------------------------------------------------------------------- #
# AL007 adjustment-mismatch
# --------------------------------------------------------------------------- #


def test_a_vendor_adjusted_series_that_disagrees_is_an_error():
    rows = [bar(0, 1000), bar(1, 1005)]
    vendor = ((WEEK[0], Decimal("900")), (WEEK[1], Decimal("1005")))
    findings = by_code(run(rows, vendor=vendor), "AL007")
    assert len(findings) == 1
    assert findings[0].details["recomputed"] == "1000"


def test_a_matching_vendor_series_produces_no_finding():
    rows = [bar(0, 1000), bar(1, 1005)]
    vendor = ((WEEK[0], Decimal("1000")), (WEEK[1], Decimal("1005")))
    assert by_code(run(rows, vendor=vendor), "AL007") == []


# --------------------------------------------------------------------------- #
# AL008 ohlc-inconsistency
# --------------------------------------------------------------------------- #


def test_inverted_range_is_an_error():
    rows = [bar(0, 1000, open=1000, high=990, low=1010)]
    assert by_code(run(rows), "AL008")


def test_non_positive_close_and_negative_volume_are_errors():
    rows = [bar(0, 0), bar(1, 1000, volume=-5)]
    messages = [f.message for f in by_code(run(rows), "AL008")]
    assert any("non-positive close" in m for m in messages)
    assert any("negative volume" in m for m in messages)


# --------------------------------------------------------------------------- #
# AL009 frozen-price-streak
# --------------------------------------------------------------------------- #


def test_five_identical_closes_look_like_a_dead_feed():
    rows = [bar(i, 1000) for i in range(6)]
    findings = by_code(run(rows), "AL009")
    assert len(findings) == 1
    assert findings[0].details["sessions"] == "6"


def test_two_identical_closes_are_normal():
    rows = [bar(0, 1000), bar(1, 1000), bar(2, 1010), bar(3, 1020)]
    assert by_code(run(rows), "AL009") == []


# --------------------------------------------------------------------------- #
# AL010 base-price-disagreement
# --------------------------------------------------------------------------- #


def test_a_declared_base_price_that_disagrees_with_the_series_warns():
    action = act(ActionKind.BONUS, 1, bonus_ratio="0.5", previous_close="2000")
    findings = by_code(run([bar(0, 1000), bar(1, 665)], [action]), "AL010")
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].details["declared"] == "2000"


def test_a_declared_base_price_matching_the_series_is_silent():
    action = act(ActionKind.BONUS, 1, bonus_ratio="0.5", previous_close="1000")
    assert by_code(run([bar(0, 1000), bar(1, 665)], [action]), "AL010") == []


# --------------------------------------------------------------------------- #
# configuration and ordering
# --------------------------------------------------------------------------- #


def test_rules_can_be_disabled_by_code():
    rows = [bar(0, 1000), bar(1, 1200)]
    assert codes(run(rows, config=AuditConfig(disabled=frozenset({"AL001"})))) == []


def test_the_enabled_allowlist_wins():
    rows = [bar(0, 1000)] + [bar(i, 1200) for i in range(1, 6)]  # 20% jump + a 5-bar freeze
    findings = run(rows, config=AuditConfig(enabled=frozenset({"AL009"})))
    assert set(codes(findings)) == {"AL009"}


def test_findings_come_back_sorted_by_symbol_date_and_code():
    rows = [bar(0, 1000), bar(1, 1200), bar(2, 1200), bar(3, 1200), bar(3, 1200)]
    keys = [(f.symbol, f.on, f.code) for f in run(rows)]
    assert keys == sorted(keys)


def test_the_price_ladder_selects_the_band():
    ladder = MarketProfile(
        name="ladder",
        weekend_days=(5, 6),
        band_ladder=((Decimal("1000"), Decimal("0.20")), (None, Decimal("0.05"))),
        default_band=Decimal("0.02"),
    )
    assert ladder.band_limit(Decimal("900")) == Decimal("0.20")
    assert ladder.band_limit(Decimal("5000")) == Decimal("0.05")
    assert ladder.validate() is None


def test_a_malformed_ladder_is_rejected():
    broken = MarketProfile(
        name="broken",
        band_ladder=((Decimal("1000"), Decimal("0.2")), (Decimal("500"), Decimal("0.1"))),
    )
    with pytest.raises(ProfileError, match="must ascend"):
        broken.validate()

