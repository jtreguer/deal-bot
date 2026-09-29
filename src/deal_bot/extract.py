"""LLM extraction of a structured spec from a messy listing."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from html import unescape
from typing import Protocol

import anthropic

from deal_bot.models import ExtractedSpec, RawListing
from deal_bot.store import Store

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5"
PROMPT_VERSION = "2"
MAX_DESCRIPTION_CHARS = 20_000

SYSTEM = """You read second-hand laptop listings from European marketplaces and return the \
facts they state as structured data. Listings are in French, German, Dutch, Italian, Spanish \
or English, often machine-translated, with abbreviations ("32Go" = 32 GB, "1To" = 1 TB, \
"U7 165H" = Core Ultra 7 165H, "RTX2000 ada" = RTX 2000 Ada).

Report what the listing claims, even when a claim looks wrong or impossible for the model. \
Do not correct or complete values from your own knowledge of the product; downstream code \
checks plausibility. Use null when the listing does not say.

- cpu: give a full model number only when the listing writes one. "i7 13th gen" stays \
"i7 13th gen"; never guess the exact part.
- gpu: null when the listing does not mention graphics at all. Use "integrated" only when it \
explicitly says integrated graphics, Intel Iris/Arc, or no dedicated GPU.

Sources: the title, then the seller's structured fields, then the free-text description. \
When they disagree on CPU, GPU, RAM, SSD or model, prefer the most specific statement and \
list the disagreement in `conflicts`. `gpu_source` says where the GPU value came from.

Condition mapping: brand new or sealed -> new; opened but unused, customer return -> open_box; \
refurbished with grade A+/premium/excellent -> refurb_excellent, grade A/very good/good -> \
refurb_good, grade B/C/fair -> refurb_fair; used "like new"/"comme neuf"/"wie neu" -> \
used_like_new, used good -> used_good, used with visible wear -> used_fair; defective, for \
parts, "pour pièces", "defekt" -> for_parts.

Screen: 1920x1200, FHD+, WUXGA -> fhd_plus; 3840x2400, UHD+, 4K, OLED -> uhd_plus_oled."""


class Extractor(Protocol):
    def extract(self, listing: RawListing) -> ExtractedSpec: ...


class ExtractionFailed(Exception):
    pass


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def listing_prompt(listing: RawListing) -> str:
    description = _clean(listing.description)
    if len(description) > MAX_DESCRIPTION_CHARS:
        log.warning("%s: description truncated from %d chars", listing.key, len(description))
        description = description[:MAX_DESCRIPTION_CHARS]
    fields = "\n".join(f"- {k}: {v}" for k, v in listing.structured.items()) or "(none)"
    return f"TITLE: {listing.title}\n\nSTRUCTURED FIELDS:\n{fields}\n\nDESCRIPTION:\n{description or '(none)'}"


def content_hash(listing: RawListing, model: str) -> str:
    payload = json.dumps([PROMPT_VERSION, model, listing_prompt(listing)], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


class ClaudeExtractor:
    def __init__(self, store: Store, model: str = DEFAULT_MODEL, client: anthropic.Anthropic | None = None):
        self.store = store
        self.model = model
        self.client = client or anthropic.Anthropic()

    def extract(self, listing: RawListing) -> ExtractedSpec:
        h = content_hash(listing, self.model)
        if cached := self.store.get_extraction(h):
            return cached
        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=2048,
                system=SYSTEM,
                messages=[{"role": "user", "content": listing_prompt(listing)}],
                output_format=ExtractedSpec,
            )
        except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError) as e:
            # Transient after the SDK's own retries: skip this listing, keep the run going.
            raise ExtractionFailed(f"API unavailable: {e.__class__.__name__}") from e
        if response.stop_reason != "end_turn" or response.parsed_output is None:
            raise ExtractionFailed(f"stop_reason={response.stop_reason}")
        spec = response.parsed_output
        self.store.put_extraction(h, self.model, spec)
        return spec
