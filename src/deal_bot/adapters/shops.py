"""Generic adapters for Shopify and WooCommerce refurbishers.

Both platforms expose a public catalogue: Shopify at /products.json and WooCommerce at
/wp-json/wc/store/v1/products. One config entry per store in sources.yaml is enough.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from deal_bot.adapters.base import Http
from deal_bot.config import Target
from deal_bot.models import Delivery, Location, Money, Protection, RawListing, SellerInfo, SellerType, SourcePolicy


@dataclass
class ShopConfig:
    name: str
    base_url: str
    country: str
    currency: str
    delivery: str = "ships_fr"
    price_includes_local_vat: bool = True
    export_zero_rated: str = "unknown"
    shipping_estimate_eur: float | None = None
    carrier: str | None = None  # dhl | ups | fedex | postal, when the shop names its carrier

    def policy(self) -> SourcePolicy:
        return SourcePolicy(
            price_includes_local_vat=self.price_includes_local_vat,
            export_zero_rated=self.export_zero_rated,
            shipping_estimate_eur=None
            if self.shipping_estimate_eur is None
            else Decimal(str(self.shipping_estimate_eur)),
            carrier=self.carrier,
        )

    def listing(self, **kw) -> RawListing:
        return RawListing(
            source=self.name,
            fetched_at=datetime.now(UTC),
            delivery=Delivery(self.delivery),
            protection=Protection.RETAILER,
            location=Location(country=self.country),
            seller=SellerInfo(name=self.name, type=SellerType.BUSINESS),
            policy=self.policy(),
            **kw,
        )


def _query_words(target: Target) -> list[re.Pattern]:
    # "precision 5680" -> match titles containing 5680 with "precision" anywhere
    return [re.compile(r"\b" + q.split()[-1] + r"\b") for q in target.queries]


class ShopifyAdapter:
    tier = "A"

    def __init__(self, cfg: ShopConfig, max_pages: int = 20):
        self.cfg = cfg
        self.name = cfg.name
        self.max_pages = max_pages

    def parse(self, products: list[dict], target: Target) -> Iterable[RawListing]:
        wanted = _query_words(target)
        for p in products:
            title = p["title"]
            if not any(w.search(title) for w in wanted):
                continue
            for v in p["variants"]:
                variant = v.get("title") or ""
                yield self.cfg.listing(
                    native_id=str(v["id"]),
                    url=f"{self.cfg.base_url}/products/{p['handle']}?variant={v['id']}",
                    title=title if variant == "Default Title" else f"{title} / {variant}",
                    price=Money(
                        amount=Decimal(str(v["price"])),
                        currency=self.cfg.currency,
                        vat_included=self.cfg.price_includes_local_vat,
                    ),
                    structured={
                        "variant": variant,
                        "product_type": p.get("product_type") or "",
                        "tags": ", ".join(p.get("tags") or []),
                    },
                    description=p.get("body_html"),
                    images=[i["src"] for i in p.get("images", [])[:5]],
                    available=bool(v.get("available")),
                )

    def search(self, target: Target, http: Http) -> Iterable[RawListing]:
        for page in range(1, self.max_pages + 1):
            products = http.get(f"{self.cfg.base_url}/products.json", limit=250, page=page).json()["products"]
            if not products:
                return
            yield from self.parse(products, target)


class WooCommerceAdapter:
    tier = "A"

    def __init__(self, cfg: ShopConfig):
        self.cfg = cfg
        self.name = cfg.name

    def parse(self, products: list[dict], target: Target) -> Iterable[RawListing]:
        wanted = _query_words(target)
        for p in products:
            title = p["name"]
            if not any(w.search(title) for w in wanted):
                continue
            prices = p["prices"]
            amount = Decimal(prices["price"]) / (10 ** prices["currency_minor_unit"])
            attrs = {a["name"]: ", ".join(t["name"] for t in a.get("terms", [])) for a in p.get("attributes", [])}
            yield self.cfg.listing(
                native_id=str(p["id"]),
                url=p["permalink"],
                title=title,
                price=Money(
                    amount=amount, currency=prices["currency_code"], vat_included=self.cfg.price_includes_local_vat
                ),
                structured=attrs | {"short_description": p.get("short_description") or ""},
                description=p.get("description"),
                images=[i["src"] for i in p.get("images", [])[:5]],
                available=bool(p.get("is_in_stock")) and bool(p.get("is_purchasable", True)),
            )

    def search(self, target: Target, http: Http) -> Iterable[RawListing]:
        seen: set[str] = set()
        for q in target.queries:
            products = http.get(f"{self.cfg.base_url}/wp-json/wc/store/v1/products", search=q, per_page=100).json()
            for listing in self.parse(products, target):
                if listing.native_id not in seen:
                    seen.add(listing.native_id)
                    yield listing
