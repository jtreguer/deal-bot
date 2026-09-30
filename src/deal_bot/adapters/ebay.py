"""eBay Browse API across the European marketplaces.

One search per marketplace and query, restricted to the laptop category. The same item
shows up on several marketplaces, so results are merged by legacy item ID. getItem is
called only for listings that could be a target laptop: it adds the item specifics, the
description, shipping to the buyer and eBay's quoted import charges.

The developer account opted out of marketplace account deletion notifications as "not
persisting eBay data". Seller usernames, and the legal name and address getItem returns
for business sellers, must therefore never be copied into a RawListing. Sellers also
write them into their description templates (shop links, logos, contact blocks), so the
description is redacted too. Free text a private seller types about themselves cannot be
caught this way.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import quote

import httpx

from deal_bot.adapters.base import Http
from deal_bot.config import Target
from deal_bot.filters import looks_like_part
from deal_bot.models import Delivery, Location, Money, Protection, RawListing, SellerInfo, SellerType, SourcePolicy

log = logging.getLogger(__name__)

API = "https://api.ebay.com"
TOKEN_URL = f"{API}/identity/v1/oauth2/token"
SCOPE = "https://api.ebay.com/oauth/api_scope"
PAGE_SIZE = 200
MAX_OFFSET = 1000
_SELLER_TYPE = {"BUSINESS": SellerType.BUSINESS, "INDIVIDUAL": SellerType.PRIVATE}
_OUT_OF_STOCK = "OUT_OF_STOCK"


def _money(m: dict | None, vat_included: bool | None = None, viewer: str | None = None) -> Money | None:
    """Amount in the seller's currency: a marketplace shows foreign items converted."""
    if not m or "value" not in m:
        return None
    value, currency = m.get("convertedFromValue", m["value"]), m.get("convertedFromCurrency", m["currency"])
    return Money(amount=Decimal(value), currency=currency, vat_included=vat_included, viewer_location=viewer)


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _ships_to(ship_to: dict | None, country: str) -> bool | None:
    if not ship_to:
        return None
    ids = lambda key: {r.get("regionId") for r in ship_to.get(key, [])}  # noqa: E731
    if country in ids("regionExcluded"):
        return False
    return bool(ids("regionIncluded") & {country, "EUROPE", "WORLDWIDE"})


def _identity_terms(seller: dict) -> list[str]:
    legal = seller.get("sellerLegalInfo") or {}
    first, last = legal.get("legalContactFirstName"), legal.get("legalContactLastName")
    terms = [
        seller.get("username"),
        legal.get("name"),
        f"{first} {last}" if first and last else None,
        last,
        legal.get("email"),
        legal.get("phone"),
        legal.get("fax"),
        legal.get("registrationNumber"),
        (legal.get("sellerProvidedLegalAddress") or {}).get("addressLine1"),
        *(v.get("vatId") for v in legal.get("vatDetails", [])),
    ]
    # Longest first, so a full name is replaced before its last name. Short terms would hit ordinary words.
    return sorted({t.strip() for t in terms if t and len(t.strip()) >= 4}, key=len, reverse=True)


def redact(text: str | None, seller: dict) -> str | None:
    if not text:
        return text
    for term in _identity_terms(seller):
        text = re.sub(re.escape(term), "[seller]", text, flags=re.I)
    return text


def _carrier(option: dict) -> str | None:
    """Which carrier clears the parcel in France, from eBay's carrier and service codes."""
    code = f"{option.get('shippingCarrierCode', '')} {option.get('shippingServiceCode', '')}".upper()
    for key, carrier in (("DHL", "dhl"), ("UPS", "ups"), ("FEDEX", "fedex"), ("USPS", "postal"), ("ROYAL", "postal")):
        if key in code:
            return carrier
    return None


def _cheapest(options: list[dict]) -> dict | None:
    priced = [o for o in options if o.get("shippingCost")]
    return min(priced, key=lambda o: Decimal(o["shippingCost"]["value"]), default=None)


def parse_item(summary: dict, detail: dict | None, buyer_country: str, viewer: str) -> RawListing:
    """Build a listing from a search summary, enriched by the getItem response when there is one."""
    item = summary | (detail or {})
    location = item.get("itemLocation") or {}
    country, postcode = location.get("country", ""), location.get("postalCode")
    seller = item.get("seller") or {}
    taxes = item.get("taxes") or []
    vat_included = True if any(t.get("includedInPrice") for t in taxes if t.get("taxType") == "VAT") else None

    options = item.get("shippingOptions") or []
    option = _cheapest(options)
    ships = _ships_to(item.get("shipToLocations"), buyer_country)
    if options:  # the search runs with the buyer as contextual location
        delivery = Delivery.SHIPS_FR
    elif country == buyer_country:
        delivery = Delivery.PICKUP
    elif ships is False:
        delivery = Delivery.NO_FR
    else:
        delivery = Delivery.UNKNOWN

    auction = "AUCTION" in item.get("buyingOptions", [])
    bid = item.get("currentBidPrice") if auction else None
    price = _money(bid, vat_included, viewer) or _money(item["price"], vat_included, viewer)
    availability = (item.get("estimatedAvailabilities") or [{}])[0].get("estimatedAvailabilityStatus")
    structured = {a["name"]: a["value"] for a in item.get("localizedAspects", [])}
    if item.get("condition"):
        structured["condition"] = item["condition"]
    images = [i["imageUrl"] for i in [item.get("image") or {}, *item.get("additionalImages", [])] if "imageUrl" in i]

    return RawListing(
        source="ebay",
        native_id=item["legacyItemId"],
        url=item["itemWebUrl"].split("?")[0],
        fetched_at=datetime.now(UTC),
        title=item["title"],
        price=price,
        shipping=_money(option["shippingCost"]) if option else None,
        import_charges=_money(option.get("importCharges")) if option else None,
        delivery=delivery,
        protection=Protection.PLATFORM,
        # eBay masks postcodes ("45***"); a masked one cannot place the item in a pickup zone.
        location=Location(country=country, postcode=postcode if postcode and "*" not in postcode else None),
        # No username: see the module docstring.
        seller=SellerInfo(
            type=_SELLER_TYPE.get(seller.get("sellerAccountType", ""), SellerType.UNKNOWN),
            feedback_count=seller.get("feedbackScore"),
            feedback_pct=float(seller["feedbackPercentage"]) if seller.get("feedbackPercentage") else None,
        ),
        structured=structured,
        description=redact(item.get("description") or item.get("shortDescription"), seller),
        images=images[:5],
        is_auction=auction,
        bids=item.get("bidCount"),
        auction_ends_at=_dt(item.get("itemEndDate")),
        available=availability != _OUT_OF_STOCK,
        policy=SourcePolicy(
            via_ebay_international_shipping=bool(option) and option.get("fulfilledThrough") == "GLOBAL_SHIPPING",
            carrier=_carrier(option) if option else None,
        ),
    )


class EbayAdapter:
    name = "ebay"
    tier = "A"

    def __init__(self, marketplaces: list[str], category_ids: str, interval: float = 0.5):
        self.marketplaces = marketplaces
        self.category_ids = category_ids
        self.interval = interval
        self._token: str | None = None

    def _auth(self, http: Http) -> str:
        if self._token is None:
            client_id, secret = os.environ.get("EBAY_CLIENT_ID"), os.environ.get("EBAY_CLIENT_SECRET")
            if not client_id or not secret:
                raise RuntimeError("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET must be set")
            resp = http.client.post(
                TOKEN_URL, auth=(client_id, secret), data={"grant_type": "client_credentials", "scope": SCOPE}
            )
            resp.raise_for_status()
            self._token = resp.json()["access_token"]
        return self._token

    def _get(self, http: Http, path: str, marketplace: str, target: Target, **params) -> dict:
        headers = {
            "Authorization": f"Bearer {self._auth(http)}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace,
            # Shipping and import charges are then quoted to the buyer's postcode.
            "X-EBAY-C-ENDUSERCTX": "contextualLocation="
            + quote(f"country={target.buyer.country},zip={target.buyer.postcode}", safe=""),
        }
        return http.get(f"{API}/buy/browse/v1/{path}", headers=headers, robots=False, **params).json()

    def _search(self, http: Http, marketplace: str, q: str, target: Target) -> Iterable[dict]:
        offset = 0
        while True:
            page = self._get(
                http, "item_summary/search", marketplace, target,
                q=q, category_ids=self.category_ids, limit=PAGE_SIZE, offset=offset,
            )  # fmt: skip
            yield from page.get("itemSummaries", [])
            offset += PAGE_SIZE
            if offset >= page.get("total", 0) or offset >= MAX_OFFSET:
                return

    def search(self, target: Target, http: Http) -> Iterable[RawListing]:
        http.host_intervals.setdefault("api.ebay.com", self.interval)
        found: dict[str, tuple[str, dict]] = {}  # legacy ID -> (marketplace, summary)
        for marketplace in self.marketplaces:
            for q in target.queries:
                for s in self._search(http, marketplace, q, target):
                    # Prefer the item's home marketplace: its URL and language match the seller's.
                    if s["legacyItemId"] not in found or s.get("listingMarketplaceId") == marketplace:
                        found[s["legacyItemId"]] = (marketplace, s)

        numbers = [re.compile(r"\b" + q.split()[-1] + r"\b") for q in target.queries]
        viewer = f"{target.buyer.country}-{target.buyer.postcode}"
        for legacy_id, (marketplace, summary) in found.items():
            detail = None
            title = summary["title"]
            if any(n.search(title) for n in numbers) and not looks_like_part(title):
                try:
                    detail = self._get(http, f"item/{quote(summary['itemId'])}", marketplace, target)
                except httpx.HTTPStatusError as e:  # ended between search and getItem, or a bad item
                    log.warning("ebay getItem %s: %s", legacy_id, e.response.status_code)
            yield parse_item(summary, detail, target.buyer.country, viewer)
