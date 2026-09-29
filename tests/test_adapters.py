import json
from decimal import Decimal

from conftest import FIXTURES

from deal_bot.adapters.refurbed import parse_product
from deal_bot.adapters.shops import ShopConfig, ShopifyAdapter, WooCommerceAdapter
from deal_bot.models import Delivery, Protection


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
