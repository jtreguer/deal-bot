import json
from decimal import Decimal

import httpx
from conftest import FIXTURES

from deal_bot.adapters.base import Http
from deal_bot.adapters.ebay import EbayAdapter, parse_item
from deal_bot.adapters.refurbed import parse_product
from deal_bot.adapters.shops import ShopConfig, ShopifyAdapter, WooCommerceAdapter
from deal_bot.models import Delivery, Protection, SellerType


def _cfg(**kw):
    return ShopConfig(**({"name": "shop", "base_url": "https://shop.test", "country": "GB", "currency": "GBP"} | kw))


def test_shopify_keeps_only_target_models(target):
    products = json.loads((FIXTURES / "shopify_cybist.json").read_text())["products"]
    listings = list(ShopifyAdapter(_cfg(name="cybist", shipping_estimate_eur=60)).parse(products, target))
    assert {"5680", "5690"} == {n for n in ("5680", "5690") for x in listings if n in x.title}
    first = listings[0]
    assert "32GB" in first.title and "RTX 2000 ADA" in first.title  # variant title is appended
    assert first.price.currency == "GBP"
    assert first.protection == Protection.RETAILER
    assert first.policy.shipping_estimate_eur == Decimal(60)
    assert first.url.startswith("https://shop.test/products/")
    assert {x.available for x in listings} == {True, False}


def test_woocommerce_parses_prices_and_matches_models(target):
    products = json.loads((FIXTURES / "woocommerce_siliconconnect.json").read_text())
    listings = list(WooCommerceAdapter(_cfg()).parse(products, target))
    assert [x.title for x in listings] == ["Dell Precision 5690 Palmrest UK Backlit Layout C9FMN"]
    assert listings[0].price.amount < Decimal(1000)  # minor units converted


def test_refurbed_product_page():
    html = (FIXTURES / "refurbed_de_5680.html").read_text()
    url = "https://www.refurbed.de/p/dell-precision-5680-13800h-16/205348aa/"
    listing = parse_product(html, url, "de")
    assert listing.title.startswith("Dell Precision 5680")
    assert listing.price.amount == Decimal(1165)
    assert listing.structured["Grafikkarte"] == "Intel Iris Xe Graphics"
    assert listing.structured["Arbeitsspeicher Größe"] == "16.0 GB"
    assert listing.delivery == Delivery.UNKNOWN  # only .fr is known to ship to France
    assert listing.available


def _ebay_fixtures():
    search = json.loads((FIXTURES / "ebay_search_de.json").read_text())
    items = json.loads((FIXTURES / "ebay_items.json").read_text())
    return {s["legacyItemId"]: s for s in search["itemSummaries"]}, items


def test_ebay_business_seller_with_details():
    summaries, items = _ebay_fixtures()
    x = parse_item(summaries["147226012324"], items["147226012324"], "FR", "FR-75001")
    assert x.source == "ebay" and x.native_id == "147226012324"
    assert x.url == "https://www.ebay.de/itm/147226012324"
    assert (x.price.amount, x.price.currency, x.price.vat_included) == (Decimal("749.00"), "EUR", True)
    assert x.price.viewer_location == "FR-75001"
    assert x.shipping.amount == Decimal("15.90")
    assert x.delivery == Delivery.SHIPS_FR
    assert x.protection == Protection.PLATFORM
    assert x.seller.type == SellerType.BUSINESS and x.seller.feedback_count == 23517
    assert x.structured["condition"] == "Gut - Refurbished"
    assert len(x.structured) > 5  # item specifics
    assert x.location.postcode is None  # "45***" is masked


def test_ebay_never_keeps_seller_identity():
    summaries, items = _ebay_fixtures()
    x = parse_item(summaries["147226012324"], items["147226012324"], "FR", "FR-75001")
    dump = x.model_dump_json().lower()
    for term in ("fake-seller", "beispiel handels", "mustermann", "shop@example.test", "1234567", "musterstrasse"):
        assert term not in dump
    assert x.seller.name is None
    assert "[seller]" in x.description and "RTX A1000" in x.description


def test_ebay_uk_item_via_international_shipping():
    summaries, items = _ebay_fixtures()
    x = parse_item(summaries["198489740505"], items["198489740505"], "FR", "FR-75001")
    # Found on ebay.de, shown there in EUR; kept in the seller's currency.
    assert (x.price.amount, x.price.currency) == (Decimal("2539.00"), "GBP")
    assert x.import_charges.currency == "GBP" and x.import_charges.amount > 0
    assert x.policy.via_ebay_international_shipping
    assert x.location.country == "GB"


def test_ebay_item_not_shipping_to_france():
    summaries, items = _ebay_fixtures()
    x = parse_item(summaries["395461180737"], items["395461180737"], "FR", "FR-75001")
    assert x.delivery == Delivery.NO_FR
    assert x.shipping is None


def test_ebay_summary_only():
    summaries, _ = _ebay_fixtures()
    x = parse_item(summaries["398090174263"], None, "FR", "FR-75001")
    assert x.seller.type == SellerType.PRIVATE and x.seller.name is None
    assert x.delivery == Delivery.SHIPS_FR
    assert x.price.vat_included is None


def test_ebay_search_merges_marketplaces_and_skips_parts(target, monkeypatch):
    summaries, items = _ebay_fixtures()
    search = (FIXTURES / "ebay_search_de.json").read_text()
    detail_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
        assert request.headers["Authorization"] == "Bearer t"
        if request.url.path.endswith("/item_summary/search"):
            return httpx.Response(200, text=search)
        legacy = request.url.path.split("%7C")[1] if "%7C" in request.url.path else request.url.path.split("|")[1]
        detail_calls.append(legacy)
        return httpx.Response(200, json=items[legacy]) if legacy in items else httpx.Response(404, json={})

    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    http = Http(min_interval=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    adapter = EbayAdapter(["EBAY_DE", "EBAY_FR"], "177", interval=0)
    listings = list(adapter.search(target, http))
    assert sorted(x.native_id for x in listings) == sorted(summaries)  # two marketplaces, no duplicates
    assert "100000000001" not in detail_calls  # battery: no getItem
    assert len(detail_calls) == len(set(detail_calls)) == 4
    assert all(x.seller.name is None for x in listings)
