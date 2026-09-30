from datetime import date
from decimal import Decimal

from conftest import TODAY, make_listing, make_spec

from deal_bot.fairvalue import fair_value
from deal_bot.landed import clearance_fee, landed_cost
from deal_bot.models import Condition, Delivery, Money, Screen, SourcePolicy


def test_rates_from_ecb_fixture(rates):
    assert rates.day == "2026-09-29"
    assert rates.to_eur(Decimal("100"), "EUR") == Decimal("100")
    assert Decimal("1.16") < rates.to_eur(Decimal(1), "GBP") < Decimal("1.18")


def test_landed_eu_seller(rates):
    listing = make_listing(price="1100", shipping=Money(amount=Decimal("17.49"), currency="EUR"))
    lc = landed_cost(listing, rates)
    assert lc.total_eur == Decimal("1117.49")
    assert not lc.estimated


def test_landed_pickup_ignores_shipping(rates):
    listing = make_listing(price="1250", delivery=Delivery.PICKUP, shipping=Money(amount=Decimal(10), currency="EUR"))
    assert landed_cost(listing, rates).total_eur == Decimal("1250.00")


def test_landed_uk_via_ebay_international_shipping(rates):
    # Survey item 287540393467: GBP 899.99 + 62.37 shipping, import VAT collected by eBay.
    listing = make_listing(
        price="899.99",
        currency="GBP",
        country="GB",
        shipping=Money(amount=Decimal("62.37"), currency="GBP"),
        policy=SourcePolicy(price_includes_local_vat=False, via_ebay_international_shipping=True),
    )
    lc = landed_cost(listing, rates)
    goods = rates.to_eur(Decimal("962.36"), "GBP")
    assert abs(lc.total_eur - goods * Decimal("1.2")) < Decimal("0.05")
    assert lc.estimated
    assert not any("carrier" in label for label, _, _ in lc.parts)


def test_landed_uk_retailer_vat_unknown_keeps_worst_case(rates):
    listing = make_listing(
        price="2015",
        currency="GBP",
        country="GB",
        policy=SourcePolicy(export_zero_rated="unknown", shipping_estimate_eur=Decimal(60)),
    )
    lc = landed_cost(listing, rates)
    price_eur = rates.to_eur(Decimal(2015), "GBP")
    expected = price_eur + 60 + (price_eur + 60) * Decimal("0.2") + Decimal("22.14")  # UPS minimum, carrier unknown
    assert abs(lc.total_eur - expected) < Decimal("0.05")
    assert any("GB VAT may be refunded" in n for n in lc.notes)


def test_landed_uk_retailer_zero_rated_strips_uk_vat(rates):
    listing = make_listing(
        price="2015",
        currency="GBP",
        country="GB",
        policy=SourcePolicy(export_zero_rated="yes", shipping_estimate_eur=Decimal(0)),
    )
    lc = landed_cost(listing, rates)
    ex_vat = rates.to_eur(Decimal(2015), "GBP") / Decimal("1.2")
    assert abs(lc.total_eur - (ex_vat * Decimal("1.2") + Decimal("22.14"))) < Decimal("0.05")


def test_fair_value_reference_config_is_base(kb, prices):
    spec = make_spec()
    fv = fair_value(spec, kb.validate(spec, ""), prices, TODAY)
    assert fv.value_eur == Decimal(1250)
    assert fv.assumptions == []


def test_fair_value_survey_3500_oled(kb, prices):
    # eBay 398090174263: i9, 32 GB, RTX 3500 Ada, OLED, 1 TB, near new, ProSupport to Aug 2027.
    spec = make_spec(
        cpu="i9-13900H",
        gpu="3500 ADA",
        screen=Screen.UHD_PLUS_OLED,
        condition=Condition.USED_LIKE_NEW,
        warranty_until=date(2027, 8, 31),
    )
    fv = fair_value(spec, kb.validate(spec, ""), prices, TODAY)
    assert fv.value_eur == Decimal(1250 + 550 + 80 + 150 + 50 + 11 * 15)


def test_fair_value_records_assumptions(kb, prices):
    spec = make_spec(screen=Screen.UNKNOWN, ssd_gb=None, cpu=None)
    fv = fair_value(spec, kb.validate(spec, ""), prices, TODAY)
    assert fv.value_eur == Decimal(1250)
    assert len(fv.assumptions) == 3


def test_landed_swiss_seller_zero_rated_strips_swiss_vat(rates):
    listing = make_listing(
        price="1081",
        currency="CHF",
        country="CH",
        policy=SourcePolicy(export_zero_rated="yes", shipping_estimate_eur=Decimal(0)),
    )
    lc = landed_cost(listing, rates)
    ex_vat = rates.to_eur(Decimal(1000), "CHF")
    assert abs(lc.total_eur - (ex_vat * Decimal("1.2") + Decimal("22.14"))) < Decimal("0.05")


def test_clearance_fee_minimum_and_share():
    assert clearance_fee(Decimal(300), "dhl") == Decimal("16.67") * Decimal("1.2")  # 1.8% of 300 < minimum
    assert clearance_fee(Decimal(1000), "ups") == Decimal("30.5") * Decimal("1.2")  # 3.05% of 1000 > minimum
    assert clearance_fee(Decimal(1000), "postal").quantize(Decimal("0.01")) == Decimal("8.00")
    assert clearance_fee(Decimal(300), None) == clearance_fee(Decimal(300), "ups")


def test_landed_us_seller_by_ups(rates):
    listing = make_listing(
        price="1680",
        currency="USD",
        country="US",
        shipping=Money(amount=Decimal("185.93"), currency="USD"),
        policy=SourcePolicy(carrier="ups"),
    )
    lc = landed_cost(listing, rates)
    base = rates.to_eur(Decimal("1865.93"), "USD")
    vat = base * Decimal("0.2")
    assert abs(lc.total_eur - (base + vat + clearance_fee(vat, "ups"))) < Decimal("0.05")
    fee = next(part for part in lc.parts if "clearance" in part[0])
    assert fee[0] == "ups clearance fee" and not fee[2]  # carrier known, not estimated
