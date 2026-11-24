"""Market profiles: JSON loading, round-trips and validation."""

import json
from decimal import Decimal

import pytest

from adjlint.market import PROFILES, MarketProfile, ProfileError, load_profile

ISSUE_EXAMPLE = {
    "name": "bse",
    "timezone": "Asia/Kolkata",
    "weekend_days": [6],
    "band_ladder": [["250", "0.05"], [None, "0.02"]],
    "price_tick": "0.05",
}


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_every_bundled_profile_survives_a_json_round_trip(name, tmp_path):
    original = PROFILES[name]
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(original.to_dict()), encoding="utf-8")
    assert load_profile(str(path)) == original


def test_omitted_keys_keep_their_defaults():
    profile = MarketProfile.from_dict(ISSUE_EXAMPLE)
    assert profile.weekend_days == (6,)
    assert profile.band_ladder == ((Decimal("250"), Decimal("0.05")), (None, Decimal("0.02")))
    assert profile.price_tick == Decimal("0.05")
    assert profile.default_band == Decimal("0.20")
    assert profile.currency == profile.description == ""
    bare = MarketProfile.from_dict({"name": "bare"})
    assert bare.weekend_days == (5, 6) and bare.band_ladder == ()


def test_null_means_absent_not_zero():
    profile = MarketProfile.from_dict({"name": "x", "price_tick": None, "band_ladder": [[None, "0.1"]]})
    assert profile.price_tick is None
    assert profile.band_ladder == ((None, Decimal("0.1")),)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"default_band": 0.05}, 'default_band must be a decimal written as a string, such as "0.05", got 0.05'),
        ({"band_ladder": [["250", 0.05]]}, "band_ladder[0] band must be a decimal written as a string"),
        ({"band_ladder": [["250"]]}, "band_ladder[0] must be a [bound, band] pair"),
        ({"band_ladder": [["abc", "0.05"]]}, "band_ladder[0] bound: 'abc' is not a decimal number"),
        ({"price_tick": "NaN"}, "price_tick: 'NaN' is not a finite number"),
        ({"weekend_days": [True]}, "weekend_days must be a list of integers"),
        ({"timezone": 5}, "timezone must be a string"),
        ({"band_lader": []}, "unknown field(s): band_lader"),
    ],
)
def test_a_bad_field_is_named(change, message):
    with pytest.raises(ProfileError) as caught:
        MarketProfile.from_dict({**ISSUE_EXAMPLE, **change})
    assert message in str(caught.value)


def test_name_is_required_and_the_top_level_must_be_an_object():
    with pytest.raises(ProfileError, match="missing required field 'name'"):
        MarketProfile.from_dict({"timezone": "UTC"})
    with pytest.raises(ProfileError, match="must be a JSON object"):
        MarketProfile.from_dict([])


def test_validate_names_the_bad_rung():
    profile = MarketProfile.from_dict({**ISSUE_EXAMPLE, "band_ladder": [["250", "0.05"], ["500", "1.5"]]})
    with pytest.raises(ProfileError, match=r"bse: band_ladder\[1\]: band limit 1.5 out of range"):
        profile.validate()


def test_validate_rejects_a_weekday_outside_0_to_6():
    with pytest.raises(ProfileError, match="weekend_days entry 9 must be 0-6"):
        MarketProfile(name="x", weekend_days=(9,)).validate()


@pytest.mark.parametrize("zone", ["Asia/Kolkata", "UTC", "+05:30"])
def test_validate_accepts_every_zone_form_resolve_zone_accepts(zone):
    assert MarketProfile(name="x", timezone=zone).validate() is None


@pytest.mark.parametrize("zone", ["Asia/Bogus", "+ab:cd"])
def test_validate_rejects_an_unknown_timezone(zone, monkeypatch):
    # Blaming an IANA name requires a database to blame it against.  Pin one so
    # this asserts the same thing on a machine with no tzdata installed, where
    # the code deliberately declines to guess (see the test below).
    monkeypatch.setattr("adjlint.market.available_timezones", lambda: {"UTC"})
    with pytest.raises(ProfileError, match="is not an IANA zone name"):
        MarketProfile(name="x", timezone=zone).validate()


@pytest.mark.parametrize("zone", ["+24000000000:00", "-130000000000:00"])
def test_an_out_of_range_offset_is_a_profile_error_not_an_overflow(zone, monkeypatch):
    # datetime raises OverflowError building these.  An offset needs no zone
    # database, so the value is wrong on every machine and must be blamed even
    # where there is no tzdata to consult.
    monkeypatch.setattr("adjlint.market.available_timezones", set)
    with pytest.raises(ProfileError, match="is not an IANA zone name"):
        MarketProfile(name="x", timezone=zone).validate()
    assert MarketProfile(name="x", timezone="+05:30").validate() is None


def test_the_whole_profiles_catalog_is_rejected_by_name():
    # `adjlint profiles > market.json` is the obvious first thing to try, and it
    # prints a catalog rather than one profile.  Name that mistake instead of
    # reporting every market in the catalog as an unknown field.
    catalog = {name: profile.to_dict() for name, profile in PROFILES.items()}
    with pytest.raises(ProfileError, match="catalog printed by `adjlint profiles`") as caught:
        MarketProfile.from_dict(catalog)
    assert "generic" in str(caught.value)


def test_without_a_zone_database_the_name_is_not_blamed(monkeypatch):
    # Windows without tzdata cannot tell a bogus zone from a real one.
    monkeypatch.setattr("adjlint.market.available_timezones", set)
    assert MarketProfile(name="x", timezone="Asia/Bogus").validate() is None


def test_an_unreadable_file_is_a_profile_error_naming_the_path(tmp_path):
    missing = str(tmp_path / "nope.json")
    with pytest.raises(ProfileError, match="cannot read profile file") as caught:
        load_profile(missing)
    assert missing in str(caught.value)


def test_a_loaded_profile_is_not_registered_in_the_catalog(tmp_path):
    path = tmp_path / "bse.json"
    path.write_text(json.dumps(ISSUE_EXAMPLE), encoding="utf-8")
    assert load_profile(str(path)).name == "bse"
    assert "bse" not in PROFILES
