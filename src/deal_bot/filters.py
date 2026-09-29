"""Cheap prefilter before extraction, and hard filters after it."""

from __future__ import annotations

import math
import re
from fnmatch import fnmatch

from deal_bot.config import PickupZone, Target
from deal_bot.fx import Rates
from deal_bot.knowledge import KnowledgeBase
from deal_bot.models import Delivery, RawListing, Scored, Verdict

# Words that mark a part or accessory as the item for sale, in the languages of the
# marketplaces covered. Only applied when the title has no CPU or RAM figure, so that
# "5680 i7 32GB + new battery" survives.
_PART_WORDS = re.compile(
    r"\b(akku|battery|batterie|bater[ií]a|palm\s?rest|palmrest|handauflage|repose[- ]poignets?|"
    r"motherboard|mainboard|carte m[èe]re|placa (?:base|madre)|scheda madre|moederbord|"
    r"lcd|display panel|screen assembly|[ée]cran de remplacement|matrix|klappdeckel|scharnier|"
    r"hinge|charni[èe]re|keyboard|tastatur|clavier|teclado|tastiera|toetsenbord|"
    r"heatsink|k[üu]hler|ventilateur|fan|l[üu]fter|bottom cover|bodenplatte|coque|"
    r"adapter|chargeur|netzteil|charger|caricabatterie|cargador|dock|sacoche|bag|tasche|"
    r"screws?|schrauben|vis|cable|kabel|c[âa]ble)\b",
    re.I,
)
_HAS_SPEC = re.compile(r"\b(i[3579][- ]?1[0-4]\d{3}|ultra\s?[579]|u[579]\s?\d{3}|\d{2}\s?(gb|go)\b)", re.I)


def prefilter(listing: RawListing, kb: KnowledgeBase, target: Target, rates: Rates) -> str | None:
    """Return a rejection reason, or None to keep the listing for extraction."""
    text = f"{listing.title} {' '.join(listing.structured.values())}"
    if not kb.mentions_any_model(text):
        return "no target model mentioned"
    if _PART_WORDS.search(listing.title) and not _HAS_SPEC.search(listing.title):
        return "part or accessory"
    if not listing.available:
        return "sold out or removed"
    try:
        eur = rates.to_eur(listing.price.amount, listing.price.currency)
    except ValueError:
        return f"unsupported currency {listing.price.currency}"
    if eur < target.hard_filters.price_floor_eur:
        return f"price under {target.hard_filters.price_floor_eur} EUR"
    return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def pickup_zone(listing: RawListing, zones: list[PickupZone]) -> tuple[str | None, bool]:
    """Return (zone name, decidable). decidable=False means location data is insufficient."""
    loc = listing.location
    if loc.country != "FR":
        return None, True
    decidable = True
    for z in zones:
        if z.postcodes:
            if loc.postcode is None:
                decidable = False
            elif any(fnmatch(loc.postcode, p) for p in z.postcodes):
                return z.name, True
        if z.radius_km is not None:
            if loc.lat is None or loc.lon is None:
                decidable = False
            elif _haversine_km(loc.lat, loc.lon, z.lat, z.lon) <= z.radius_km:
                return z.name, True
    return None, decidable


def apply_hard_filters(s: Scored, target: Target) -> None:
    hf = target.hard_filters
    spec, v = s.spec, s.validation

    def reject(reason: str) -> None:
        s.verdict = Verdict.REJECT
        s.verdict_reasons.append(reason)

    def hold(reason: str) -> None:
        if s.verdict != Verdict.REJECT:
            s.verdict = Verdict.HOLD
        s.verdict_reasons.append(reason)

    if spec is None:
        hold(f"extraction failed: {s.extraction_error}")
        return
    if not spec.is_complete_laptop:
        reject("not a complete laptop")
    if v.model_key not in target.models:
        reject("not a target model")
    if spec.condition.value in hf.exclude_conditions:
        reject(f"condition {spec.condition.value}")
    if spec.locked:
        reject("locked (BIOS, Computrace or MDM)")

    if spec.ram_gb is None:
        hold("RAM not stated")
    elif spec.ram_gb < hf.ram_gb_min:
        reject(f"RAM {spec.ram_gb} GB < {hf.ram_gb_min} GB")

    if v.gpu_key is None:
        hold("GPU not stated" if not spec.gpu else f"GPU {spec.gpu!r} not recognised")
    elif v.gpu_key in hf.gpu_exclude:
        reject(f"GPU {v.gpu_key} excluded")

    match s.raw.delivery:
        case Delivery.NO_FR:
            reject("does not ship to France")
        case Delivery.UNKNOWN:
            hold("delivery to France not confirmed")
        case Delivery.PICKUP:
            zone, decidable = pickup_zone(s.raw, target.pickup_zones)
            if zone:
                s.pickup_zone = zone
            elif decidable:
                reject("pickup only, outside the pickup zones")
            else:
                hold("pickup only, location needs checking")
