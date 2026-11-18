"""CSV parsing: the regional-export quirks, and the errors that must stay loud."""

from datetime import date
from decimal import Decimal
from io import StringIO

import pytest

from adjlint import ActionKind, DataError, load_actions, load_adjusted, load_calendar, load_prices
from adjlint.io import normalise_number, parse_date, parse_decimal, resolve_zone
from conftest import WEEK

PRICES = """symbol,date,open,high,low,close,volume
FOO,2026-03-02,100,110,99,105,1000
FOO,2026-03-03,105,112,104,108,1200
BAR,2026-03-02,50,55,49,52,900
"""


def test_persian_digits_become_ascii():
    assert normalise_number("\u06f1\u06f2\u06f3") == "123"
    assert parse_decimal("\u06f1\u06f0\u06f5.\u06f5") == Decimal("105.5")


def test_thousands_separators_are_stripped_only_for_digit_groups():
    assert normalise_number("1,234,567.50") == "1234567.50"
    # "1,50" may be a comma decimal separator: stripping it would silently
    # turn 1.50 into 150, so it is left alone and fails loudly instead.
    assert normalise_number("1,50") == "1,50"
    with pytest.raises(DataError, match="cannot parse"):
        parse_decimal("1,50", field="close")


def test_placeholder_cells_parse_as_missing_not_zero():
    for blank in ("", "-", "--", "N/A", "null", None):
        assert parse_decimal(blank) is None


def test_date_shapes_all_reach_the_same_session():
    for raw in ("2026-03-02", "2026/03/02", "2026.03.02", "2026-03-02T09:00:00"):
        assert parse_date(raw) == date(2026, 3, 2)


def test_a_utc_timestamp_is_floored_in_the_market_timezone():
    late = "2026-03-02T23:30:00+00:00"
    assert parse_date(late) == date(2026, 3, 2)
    # +03:30 is the Tehran offset written as a fixed offset, so the suite does not
    # depend on the IANA database -- Windows ships none.
    assert parse_date(late, timezone="+03:30") == date(2026, 3, 3)
    assert parse_date(late, timezone="UTC") == date(2026, 3, 2)


def _iana_available(name):
    try:
        resolve_zone(name)
    except DataError:
        return False
    return True


@pytest.mark.skipif(not _iana_available("Asia/Tehran"), reason="tzdata not installed")
def test_named_iana_zones_resolve_when_the_database_exists():
    assert parse_date("2026-03-02T23:30:00+00:00", timezone="Asia/Tehran") == date(2026, 3, 3)


def test_a_zone_that_cannot_be_loaded_reports_an_actionable_error():
    with pytest.raises(DataError, match="tzdata"):
        parse_date("2026-03-02T23:30:00+00:00", timezone="Mars/Olympus_Mons")


def test_an_unparsable_date_is_an_error_not_a_silent_row_drop():
    with pytest.raises(DataError, match="unrecognised date"):
        parse_date("02-03")


def test_load_prices_groups_by_symbol_and_keeps_file_order():
    by_symbol = load_prices(StringIO(PRICES))
    assert sorted(by_symbol) == ["BAR", "FOO"]
    assert [b.close for b in by_symbol["FOO"]] == [Decimal("105"), Decimal("108")]
    assert by_symbol["BAR"][0].volume == Decimal("900")


def test_a_row_without_a_close_is_refused():
    broken = "date,open\n2026-03-02,100\n"
    with pytest.raises(DataError, match="no close price"):
        load_prices(StringIO(broken))


def test_symbols_without_a_symbol_column_share_one_bucket():
    by_symbol = load_prices(StringIO("date,close\n2026-03-02,10\n2026-03-03,11\n"))
    assert list(by_symbol) == ["*"]


def test_action_kind_aliases_are_accepted():
    csv = (
        "symbol,kind,effective_date,bonus_ratio,dps\n"
        "FOO,capital_increase,2026-03-03,0.5,\n"
        "FOO,dps,2026-03-04,,20\n"
    )
    first, second = load_actions(StringIO(csv))
    assert first.kind is ActionKind.BONUS and first.bonus_ratio == Decimal("0.5")
    assert second.kind is ActionKind.DIVIDEND and second.cash_per_share == Decimal("20")


def test_an_unknown_action_kind_is_refused_rather_than_skipped():
    csv = "symbol,kind,effective_date,bonus_ratio\nFOO,stock_division,2026-03-03,0.5\n"
    with pytest.raises(DataError, match="unknown action kind"):
        load_actions(StringIO(csv))


def test_an_action_without_terms_is_refused():
    csv = "symbol,kind,effective_date\nFOO,dividend,2026-03-03\n"
    with pytest.raises(DataError, match="no economic terms"):
        load_actions(StringIO(csv))


def test_load_adjusted_and_calendar():
    adjusted = load_adjusted(StringIO("date,adjusted_close\n2026-03-02,99.5\n"))
    assert adjusted["*"] == [(date(2026, 3, 2), Decimal("99.5"))]
    calendar = load_calendar(StringIO("date\n2026-03-02\n2026-03-03\n"))
    assert calendar == (WEEK[0], WEEK[1])

