import pytest
from conftest import make_listing, make_spec

from deal_bot.filters import apply_hard_filters, pickup_zone, prefilter
from deal_bot.models import Delivery, Location, Scored, Verdict


@pytest.mark.parametrize(
    ("title", "price", "reason"),
    [
        ("Dell Precision 5690 Palmrest UK Backlit Layout C9FMN", "120", "part or accessory"),
        ("Akku für Dell Precision 5680 5690 100Wh", "90", "part or accessory"),
        ("Dell Precision 5680 i7-13800H 32GB + new battery", "1200", None),
        ("Dell Precision 5680 Klappdeckel Display komplett", "400", "part or accessory"),
        ("Dell Inspiron 5680 Gaming PC i7", "600", "no target model mentioned"),
        ("Dell Precision 5680 16 inch", "150", "price under 300 EUR"),
        ("Dell Precision 5680 i7-13800H 32GB", "1500", None),
        ("Dell Precision 5680 i7-13800H 32GB", "1500.01", "price over 1500 EUR"),
    ],
)
def test_prefilter(kb, target, rates, title, price, reason):
    assert prefilter(make_listing(title=title, price=price), kb, target, rates) == reason


def test_prefilter_drops_sold_out(kb, target, rates):
    assert prefilter(make_listing(available=False), kb, target, rates) == "sold out or removed"


@pytest.mark.parametrize(
    ("loc", "zone", "decidable"),
    [
        (Location(country="FR", city="Paris", postcode="75007"), "Paris", True),
        (Location(country="FR", city="Meudon", postcode="92190", lat=48.81, lon=2.24), None, True),
        (Location(country="FR", city="Bordeaux", postcode="33800"), "Bordeaux", True),
        (Location(country="FR", city="Anglet", postcode="64600", lat=43.48, lon=-1.52), "Biarritz", True),
        (Location(country="FR", city="Lyon", postcode="69003", lat=45.76, lon=4.84), None, True),
        (Location(country="FR", city="Somewhere"), None, False),
        (Location(country="NL", city="Varsselder"), None, True),
    ],
)
def test_pickup_zones(target, loc, zone, decidable):
    listing = make_listing(delivery=Delivery.PICKUP, location=loc)
    assert pickup_zone(listing, target.pickup_zones) == (zone, decidable)


def _scored(kb, spec, **listing_kw):
    s = Scored(raw=make_listing(**listing_kw), spec=spec)
    s.validation = kb.validate(spec, s.raw.title)
    return s


def test_hard_filters_pass(kb, target):
    s = _scored(kb, make_spec())
    apply_hard_filters(s, target)
    assert s.verdict == Verdict.PASS, s.verdict_reasons


@pytest.mark.parametrize(
    ("spec_kw", "verdict", "reason_part"),
    [
        ({"ram_gb": 16}, Verdict.REJECT, "RAM 16 GB"),
        ({"gpu": "RTX A1000 6GB"}, Verdict.REJECT, "rtx-a1000 excluded"),
        ({"gpu": "Intel Iris Xe"}, Verdict.REJECT, "integrated excluded"),
        ({"gpu": None}, Verdict.HOLD, "GPU not stated"),
        ({"ram_gb": None}, Verdict.HOLD, "RAM not stated"),
        ({"locked": True}, Verdict.REJECT, "locked"),
        ({"is_complete_laptop": False}, Verdict.REJECT, "not a complete laptop"),
        ({"model": "Precision 5570"}, Verdict.REJECT, "not a target model"),
    ],
)
def test_hard_filters_reject_or_hold(kb, target, spec_kw, verdict, reason_part):
    s = _scored(kb, make_spec(**spec_kw), title="Dell laptop")
    apply_hard_filters(s, target)
    assert s.verdict == verdict
    assert any(reason_part in r for r in s.verdict_reasons), s.verdict_reasons


def test_pickup_outside_zones_rejected(kb, target):
    loc = Location(country="NL", city="Varsselder")
    s = _scored(kb, make_spec(), delivery=Delivery.PICKUP, location=loc)
    apply_hard_filters(s, target)
    assert s.verdict == Verdict.REJECT


def test_pickup_in_paris_passes(kb, target):
    loc = Location(country="FR", city="Paris", postcode="75015")
    s = _scored(kb, make_spec(), delivery=Delivery.PICKUP, location=loc)
    apply_hard_filters(s, target)
    assert (s.verdict, s.pickup_zone) == (Verdict.PASS, "Paris")


def _filtered(kb, target, **spec_kw):
    listing = make_listing(title="Dell Precision laptop")
    spec = make_spec(**spec_kw)
    s = Scored(raw=listing, spec=spec, validation=kb.validate(spec, listing.title))
    apply_hard_filters(s, target)
    return s


def test_impossible_gpu_is_flagged_not_rejected(kb, target):
    # The 5690 never had an RTX A1000, which the target excludes. The claim is wrong, so it cannot reject.
    s = _filtered(kb, target, model="Precision 5690", cpu="Core Ultra 7 165H", gpu="RTX A1000")
    assert s.verdict == Verdict.PASS
    assert s.validation.invalid_fields == ["gpu"]


def test_impossible_ram_is_flagged_not_rejected(kb, target):
    s = _filtered(kb, target, ram_gb=24)
    assert s.verdict == Verdict.PASS
    assert s.validation.invalid_fields == ["ram"]


def test_possible_low_ram_is_still_rejected(kb, target):
    s = _filtered(kb, target, ram_gb=16)
    assert s.verdict == Verdict.REJECT and not s.validation.invalid


def test_price_ceiling_converts_currency(kb, target, rates):
    # GBP 1,300 is about EUR 1,520: over the ceiling although the number is lower.
    listing = make_listing(title="Dell Precision 5680 i7-13800H 32GB", price="1300", currency="GBP")
    assert prefilter(listing, kb, target, rates) == "price over 1500 EUR"
