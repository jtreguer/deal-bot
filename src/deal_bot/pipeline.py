"""One run: collect, prefilter, extract, validate, filter, price, score, rank."""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

from deal_bot.adapters.base import Adapter, Blocked, Http, progress
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
        """(GPU tier, count, min, max) over passing listings, from landed prices.

        Listings flagged with an impossible configuration are left out: their GPU tier is not trustworthy.
        """
        bands: dict[str, list[float]] = {}
        for s in self.items:
            if s.verdict == Verdict.PASS and s.landed and not s.duplicate_of and not s.validation.invalid:
                bands.setdefault(s.validation.gpu_key or "?", []).append(float(s.landed.total_eur))
        return sorted((k, len(v), min(v), max(v)) for k, v in bands.items())


def collect(
    adapters: list[Adapter], target: Target, kb: KnowledgeBase, http: Http
) -> tuple[list[RawListing], dict[str, str]]:
    listings: list[RawListing] = []
    status: dict[str, str] = {}
    for i, a in enumerate(adapters, 1):
        progress.info("[%d/%d] %s: collecting", i, len(adapters), a.name)
        started = time.monotonic()
        try:
            found = list(a.search(target, kb, http))
        except Blocked as e:
            status[a.name] = f"blocked: {e}"
        except Exception as e:  # one broken source must not stop the run
            log.exception("adapter %s failed", a.name)
            status[a.name] = f"error: {e.__class__.__name__}: {e}"
        else:
            status[a.name] = f"ok, {len(found)} listings"
            listings.extend(found)
        progress.info("[%d/%d] %s: %s (%.0fs)", i, len(adapters), a.name, status[a.name], time.monotonic() - started)
    return listings, status


def score_listings(
    raw: list[RawListing],
    target: Target,
    kb: KnowledgeBase,
    prices: list[PriceModel],
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
    progress.info("%d listings to extract, %d dropped by the prefilter", len(kept), prefiltered.total())
    done = 0
    done_lock = threading.Lock()

    def process(listing: RawListing) -> Scored:
        nonlocal done
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
        with done_lock:
            done += 1
            if done % 10 == 0 or done == len(kept):
                progress.info("extracted %d/%d", done, len(kept))
        return s

    with ThreadPoolExecutor(max_workers=workers) as pool:
        items = list(pool.map(process, kept.values()))
    group_duplicates(items)
    return items, prefiltered


def run(
    target: Target,
    adapters: list[Adapter],
    kb: KnowledgeBase,
    prices: list[PriceModel],
    rates: Rates,
    extractor: Extractor,
    http: Http,
    today: date | None = None,
) -> RunResult:
    today = today or date.today()
    raw, status = collect(adapters, target, kb, http)
    items, prefiltered = score_listings(raw, target, kb, prices, rates, extractor, today)
    ranked, suspicious = rank(items, target.ranking)
    return RunResult(target, rates, today, status, prefiltered, items, ranked, suspicious)
