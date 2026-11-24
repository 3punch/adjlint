"""CSV parsing: the regional-export quirks, and the errors that must stay loud."""

from datetime import date, timedelta
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


@pytest.mark.parametrize("zone", ["+24000000000:00", "-130000000000:00", "+ab:cd"])
def test_an_impossible_fixed_offset_is_a_data_error_not_a_traceback(zone):
    # Building timedelta(hours=24000000000) raises OverflowError inside datetime,
    # which escaped main() because main only guards ValueError-derived errors.
    # --timezone reaches this the same way a profile file does.
    with pytest.raises(DataError, match="not a valid fixed offset"):
        resolve_zone(zone)


def test_a_usable_fixed_offset_still_resolves():
    assert resolve_zone("+05:30").utcoffset(None) == timedelta(hours=5, minutes=30)
    assert resolve_zone("-03:30").utcoffset(None) == timedelta(hours=-3, minutes=-30)


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


def _closes(source, **kwargs):
    """Close prices by symbol, from a CSV string or from any line source."""
    if isinstance(source, str):
        source = StringIO(source)
    return {k: [bar.close for bar in bars] for k, bars in load_prices(source, **kwargs).items()}


def test_tsv_and_semicolon_exports_load_like_the_comma_original():
    expected = _closes(PRICES)
    assert _closes(PRICES.replace(",", "\t")) == expected
    assert _closes(PRICES.replace(",", ";")) == expected


def test_a_forced_delimiter_wins_over_the_sniffer():
    # A pipe-separated export is deliberately outside the sniffed set.
    assert _closes(PRICES.replace(",", "|"), delimiter="|") == _closes(PRICES)
    # The same file without the override must fail loudly, not half-load.
    with pytest.raises(DataError):
        _closes(PRICES.replace(",", "|"))


def test_a_multi_character_delimiter_is_refused():
    with pytest.raises(DataError, match="one character"):
        _closes(PRICES, delimiter=";;")


def test_an_ambiguous_sniff_reports_the_header_it_found():
    # Sniffer refuses this sample (a semicolon header over bare rows), so we
    # fall back to a comma -- and the error must show the header we parsed.
    ambiguous = "symbol;date;close\nFOO\nBAR\n"
    with pytest.raises(DataError, match="ambiguous delimiter") as exc:
        _closes(ambiguous)
    assert "symbol;date;close" in str(exc.value)


def test_a_bare_single_column_file_still_loads_after_sniff_fails():
    # The fallback must not turn every one-column calendar into an error.
    assert load_calendar(StringIO("date\n2026-03-02\n2026-03-03\n")) == (WEEK[0], WEEK[1])


def test_a_list_or_iterator_of_lines_still_loads():
    # Detection reads a sample, but DictReader has always taken any iterable of
    # lines, so sniffing must not assume readline() or seek() exist.
    lines = ["date,close\n", "2026-03-02,10\n"]
    assert _closes(lines) == {"*": [Decimal("10")]}
    assert _closes(iter(lines)) == {"*": [Decimal("10")]}
    # The same holds for a semicolon export, which has to be sniffed first.
    assert _closes(iter(["date;close\n", "2026-03-02;10\n"])) == {"*": [Decimal("10")]}


def test_a_semicolon_inside_an_unused_column_is_not_an_ambiguous_delimiter():
    # The comma parse is usable here, so the extra separator in a free-text
    # column is none of the loader's business.
    assert _closes("date,close,source;label\n2026-03-02,10\n") == {"*": [Decimal("10")]}


def test_a_stream_position_left_by_the_caller_is_respected():
    # Sampling must rewind to where the caller stopped, not to byte zero, or a
    # preamble line becomes the header.
    stream = StringIO("# generated by the exchange\ndate,close\n2026-03-02,10\n")
    assert stream.readline().startswith("#")
    assert _closes(stream) == {"*": [Decimal("10")]}


def test_a_confident_but_wrong_sniff_defers_to_the_separator_that_works():
    # Sniffer counts commas here and calls it a comma file, which has no date
    # column.  The separator that actually yields the required columns wins.
    text = "date;close;description, optional\n2026-03-02;1,000;regular\n"
    assert _closes(text) == {"*": [Decimal("1000")]}


def test_a_missing_business_column_blames_the_column_not_the_delimiter():
    # date,open is unambiguous; inventing a delimiter complaint would send the
    # user hunting for a --delimiter that cannot help them.
    with pytest.raises(DataError, match="no close price"):
        _closes("date,open\n2026-03-02,100\n")

