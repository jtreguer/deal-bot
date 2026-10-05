"""Refurbed (refurbed.fr, .de, .at, ...). Stock and price differ per country domain."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal

from selectolax.parser import HTMLParser

from deal_bot.adapters.base import Http, json_ld, progress
from deal_bot.config import Target
from deal_bot.knowledge import KnowledgeBase
from deal_bot.models import Delivery, Location, Money, Protection, RawListing, SellerInfo, SellerType, SourcePolicy

_COUNTRY = {"fr": "FR", "de": "DE", "at": "AT", "nl": "NL", "it": "IT", "be": "BE"}


def parse_product(html: str, url: str, domain: str) -> RawListing | None:
    offer = next(
        (
            d["object"]["offers"]
            for d in json_ld(html)
            if d.get("@type") == "BuyAction" and "offers" in d.get("object", {})
        ),
        None,
    )
    if offer is None:
        return None
    name = next((d["object"]["name"] for d in json_ld(html) if d.get("@type") == "BuyAction"), "")
    tree = HTMLParser(html)
    specs: dict[str, str] = {}
    for node in tree.css('[data-test="details-attribute"]'):
        dt, dd = node.css_first("dt"), node.css_first("dd")
        if dt and dd:
            specs[dt.text(strip=True)] = " ".join(dd.text(separator=" ", strip=True).split())
    slug_id = re.search(r"/p/([^/]+/[^/]+)/", url)
    return RawListing(
        source=f"refurbed-{domain}",
        native_id=slug_id.group(1) if slug_id else url,
        url=url,
        fetched_at=datetime.now(UTC),
        title=name,
        price=Money(amount=Decimal(str(offer["price"])), currency=offer.get("priceCurrency", "EUR"), vat_included=True),
        delivery=Delivery.SHIPS_FR if domain == "fr" else Delivery.UNKNOWN,
        protection=Protection.RETAILER,
        location=Location(country=_COUNTRY[domain]),
        seller=SellerInfo(name="refurbed", type=SellerType.BUSINESS),
        structured=specs,
        # Shipping within the country is normally free on Refurbed; not confirmed per listing.
        policy=SourcePolicy(shipping_estimate_eur=Decimal(0)),
        available=offer.get("availability", "").endswith("InStock"),
    )


def slug_matches(link: str, kb: KnowledgeBase) -> bool:
    """Search results include unrelated products (phones, other generations). The slug names the
    model in short form, e.g. /p/lenovo-tp-p1g5-i7-12800h/123b/ or /p/dell-precision-5680-13800h-16/456aa/."""
    return kb.mentions_any_model(re.sub(r"[-/]", " ", link))


class RefurbedAdapter:
    tier = "B"

    def __init__(self, domains: list[str]):
        self.domains = domains
        self.name = "refurbed"

    def search(self, target: Target, kb: KnowledgeBase, http: Http) -> Iterable[RawListing]:
        for domain in self.domains:
            base = f"https://www.refurbed.{domain}"
            links: set[str] = set()
            for q in target.queries:
                html = http.get(f"{base}/search/", query=q).text
                links |= {link for link in re.findall(r'href="(/p/[^"]+/)"', html) if slug_matches(link, kb)}
            progress.info("refurbed.%s: %d product pages to fetch", domain, len(links))
            for i, link in enumerate(sorted(links), 1):
                url = base + link
                progress.info("refurbed.%s: page %d/%d %s", domain, i, len(links), link)
                if listing := parse_product(http.get(url).text, url, domain):
                    yield listing
