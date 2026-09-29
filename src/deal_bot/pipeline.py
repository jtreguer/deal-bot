"""One run: collect, prefilter, extract, validate, filter, price, score, rank."""

from __future__ import annotations

import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

from deal_bot.adapters.base import Adapter, Blocked, Http
from deal_bot.config import PriceModel, Target
from deal_bot.extract import ExtractionFailed, Extractor
from deal_bot.fairvalue import fair_value
from deal_bot.filters import apply_hard_filters, prefilter
from deal_bot.fx import Rates
from deal_bot.knowledge import KnowledgeBase
from deal_bot.landed import landed_cost
from deal_bot.models import RawListing, Scored, Verdict
from deal_bot.scoring import group_duplicates, rank, risk

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    target: Target
    rates: Rates
    today: date
    source_status: dict[str, str] = field(default_factory=dict)
    prefiltered: Counter = field(default_factory=Counter)
    items: list[Scored] = field(default_factory=list)
    ranked: list[Scored] = field(default_factory=list)
    suspicious: list[Scored] = field(default_factory=list)

    @property
    def held(self) -> list[Scored]:
        return [s for s in self.items if s.verdict == Verdict.HOLD]

    @property
    def rejected_tally(self) -> Counter:
        c = Counter(self.prefiltered)
        for s in self.items:
            if s.verdict == Verdict.REJECT:
                c.update(s.verdict_reasons)
        return c

    def market_bands(self) -> list[tuple[str, int, float, float]]:
        """(GPU tier, count, min, max) over passing listings, from landed prices."""
        bands: dict[str, list[float]] = {}
        for s in self.items:
            if s.verdict == Verdict.PASS and s.landed and not s.duplicate_of:
                bands.setdefault(s.validation.gpu_key or "?", []).append(float(s.landed.total_eur))
        return sorted((k, len(v), min(v), max(v)) for k, v in bands.items())


def collect(adapters: list[Adapter], target: Target, http: Http) -> tuple[list[RawListing], dict[str, str]]:
    listings: list[RawListing] = []
    status: dict[str, str] = {}
    for a in adapters:
        try:
            found = list(a.search(target, http))
        except Blocked as e:
            status[a.name] = f"blocked: {e}"
            continue
        except Exception as e:  # one broken source must not stop the run
            log.exception("adapter %s failed", a.name)
            status[a.name] = f"error: {e.__class__.__name__}: {e}"
            continue
        status[a.name] = f"ok, {len(found)} listings"
        listings.extend(found)
    return listings, status


def score_listings(
    raw: list[RawListing],
    target: Target,
    kb: KnowledgeBase,
    prices: PriceModel,
    rates: Rates,
    extractor: Extractor,
    today: date,
    workers: int = 4,
) -> tuple[list[Scored], Counter]:
    prefiltered: Counter = Counter()
    kept: dict[str, RawListing] = {}
    for listing in raw:
        if reason := prefilter(listing, kb, target, rates):
            prefiltered[reason] += 1
        else:
            kept.setdefault(listing.key, listing)

    def process(listing: RawListing) -> Scored:
        s = Scored(raw=listing)
        try:
            s.spec = extractor.extract(listing)
        except ExtractionFailed as e:
            s.extraction_error = str(e)
        if s.spec:
            s.validation = kb.validate(s.spec, listing.title)
        apply_hard_filters(s, target)
        if s.verdict != Verdict.REJECT:
            s.landed = landed_cost(listing, rates)
            if s.spec and (fv := fair_value(s.spec, s.validation, prices, today)):
                s.fair = fv
                s.discount = float((fv.value_eur - s.landed.total_eur) / fv.value_eur)
            risk(s, today)
        return s

    with ThreadPoolExecutor(max_workers=workers) as pool:
        items = list(pool.map(process, kept.values()))
    group_duplicates(items)
    return items, prefiltered


def run(
    target: Target,
    adapters: list[Adapter],
    kb: KnowledgeBase,
    prices: PriceModel,
    rates: Rates,
    extractor: Extractor,
    http: Http,
    today: date | None = None,
) -> RunResult:
    today = today or date.today()
    raw, status = collect(adapters, target, http)
    items, prefiltered = score_listings(raw, target, kb, prices, rates, extractor, today)
    ranked, suspicious = rank(items, target.ranking)
    return RunResult(target, rates, today, status, prefiltered, items, ranked, suspicious)
