"""Risk score, ranking and cross-source duplicate grouping."""

from __future__ import annotations

from datetime import date

from deal_bot.config import Ranking
from deal_bot.models import Protection, Scored, SellerType, Verdict


def risk(s: Scored, today: date) -> None:
    points: list[tuple[int, str]] = []
    seller, spec = s.raw.seller, s.spec
    if seller.feedback_count == 0 and seller.type != SellerType.BUSINESS:
        points.append((25, "seller has no feedback"))
    elif seller.member_since and (today - seller.member_since).days < 90:
        points.append((25, "seller account under 3 months old"))
    if s.discount is not None:
        if s.discount > 0.55:
            points.append((40, f"{s.discount:.0%} under fair value: too good to be true?"))
        elif s.discount > 0.40:
            points.append((25, f"{s.discount:.0%} under fair value"))
    if s.validation.invalid:
        points.append((20, "; ".join(s.validation.invalid)))
    if s.validation.warnings:
        points.append((10, "; ".join(s.validation.warnings)))
    if spec and spec.conflicts:
        points.append((15, "listing contradicts itself: " + "; ".join(spec.conflicts)))
    if s.raw.protection == Protection.NONE:
        points.append((20, "no buyer protection"))
    if spec and spec.payment_before_inspection:
        points.append((20, "payment required before inspection"))
    if spec and spec.template_text_suspected:
        points.append((10, "description looks copied from another listing or model"))
    s.risk = min(100, sum(p for p, _ in points))
    s.risk_reasons = [r for _, r in points]


def rank(items: list[Scored], cfg: Ranking) -> tuple[list[Scored], list[Scored]]:
    """Return (ranked, suspicious). Only passing, priced, non-duplicate listings are ranked."""
    ranked, suspicious = [], []
    for s in items:
        if s.verdict != Verdict.PASS or s.discount is None or s.duplicate_of:
            continue
        penalty = cfg.protection_penalty.get(s.raw.protection.value, 0.0)
        s.adjusted_discount = s.discount - cfg.risk_weight * s.risk - penalty
        (suspicious if s.risk >= cfg.suspicious_risk else ranked).append(s)
    key = lambda s: s.adjusted_discount  # noqa: E731
    return sorted(ranked, key=key, reverse=True), sorted(suspicious, key=key, reverse=True)


def _same_ssd(a: Scored, b: Scored) -> bool:
    x, y = a.spec.ssd_gb, b.spec.ssd_gb
    return x is None or y is None or abs(x - y) <= 0.05 * max(x, y)  # 1000 vs 1024 GB


_PROTECTION_ORDER = [Protection.PLATFORM, Protection.RETAILER, Protection.PICKUP, Protection.NONE]


def group_duplicates(items: list[Scored], price_tolerance: float = 0.15) -> None:
    """Mark listings from different sources that look like the same physical unit.

    Only private sellers are grouped: two shops with the same config are two units, while a
    private seller cross-posting on eBay and a classifieds site is common. Same model, CPU,
    GPU and RAM, prices within the tolerance, and SSD sizes that agree when both listings
    state one (sellers often leave it out on one channel). The listing with the best buyer
    protection stays primary; the others point to it.
    """
    candidates = [
        s
        for s in items
        if s.spec and s.landed and s.verdict != Verdict.REJECT and s.raw.seller.type == SellerType.PRIVATE
    ]
    buckets: dict[tuple, list[Scored]] = {}
    for s in candidates:
        v = s.validation
        fp = (v.model_key, v.cpu_key, v.gpu_key, s.spec.ram_gb)
        if None in fp:
            continue
        buckets.setdefault(fp, []).append(s)
    for group in buckets.values():
        group.sort(key=lambda s: (_PROTECTION_ORDER.index(s.raw.protection), s.landed.total_eur))
        primary = group[0]
        for other in group[1:]:
            if other.raw.source == primary.raw.source or not _same_ssd(primary, other):
                continue
            lo, hi = sorted([float(primary.landed.total_eur), float(other.landed.total_eur)])
            if hi - lo <= hi * price_tolerance:
                other.duplicate_of = primary.raw.key
                primary.duplicates.append(other.raw.key)
