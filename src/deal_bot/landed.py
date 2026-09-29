"""Landed cost in EUR, VAT included, delivered to a private buyer in France."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from deal_bot.fx import Rates
from deal_bot.models import Delivery, LandedCost, RawListing

EU = {
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IE",
    "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK",
}  # fmt: skip
FR_VAT = Decimal("0.20")
# Local VAT that a non-EU seller's price may include and may refund on export.
EXPORT_VAT = {"GB": Decimal("0.20"), "CH": Decimal("0.081")}
CARRIER_IMPORT_FEE = Decimal(20)  # carrier's customs handling fee, typical for FR imports
CUSTOMS_DUTY = Decimal(0)  # laptops, tariff line 8471.30


def _eur(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def landed_cost(listing: RawListing, rates: Rates) -> LandedCost:
    parts: list[tuple[str, Decimal, bool]] = []
    notes: list[str] = []
    policy = listing.policy
    price = rates.to_eur(listing.price.amount, listing.price.currency)
    parts.append(("price", _eur(price), False))

    if listing.delivery == Delivery.PICKUP:
        return LandedCost(total_eur=_eur(price), parts=parts, notes=["pickup, no shipping"])

    if listing.shipping is not None:
        shipping = rates.to_eur(listing.shipping.amount, listing.shipping.currency)
        parts.append(("shipping", _eur(shipping), False))
    elif policy.shipping_estimate_eur is not None:
        shipping = policy.shipping_estimate_eur
        parts.append(("shipping", _eur(shipping), True))
    else:
        shipping = Decimal(0)
        notes.append("shipping cost unknown, counted as 0")

    if listing.location.country not in EU:
        goods = price
        country = listing.location.country
        local_vat = EXPORT_VAT.get(country)
        if local_vat and policy.price_includes_local_vat:
            if policy.export_zero_rated == "yes":
                goods = price / (1 + local_vat)
                parts[0] = (f"price ex {country} VAT", _eur(goods), False)
            elif policy.export_zero_rated == "unknown":
                refund = price - price / (1 + local_vat)
                notes.append(f"{country} VAT may be refunded on export: up to -{_eur(refund)} EUR")
        if listing.import_charges is not None:
            charges = rates.to_eur(listing.import_charges.amount, listing.import_charges.currency)
            parts.append(("import charges (quoted)", _eur(charges), False))
        else:
            parts.append(("customs duty", CUSTOMS_DUTY, False))
            parts.append(("FR import VAT", _eur((goods + shipping) * FR_VAT), True))
            if not policy.via_ebay_international_shipping:
                parts.append(("carrier handling fee", CARRIER_IMPORT_FEE, True))

    total = sum((amount for _, amount, _ in parts), Decimal(0))
    return LandedCost(total_eur=_eur(total), parts=parts, notes=notes)
