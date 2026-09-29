from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from deal_bot.config import ROOT, load_gpu_patterns, load_models, load_prices, load_target
from deal_bot.fx import parse_ecb
from deal_bot.knowledge import KnowledgeBase
from deal_bot.models import (
    Condition,
    Delivery,
    ExtractedSpec,
    FieldSource,
    Location,
    Money,
    Protection,
    RawListing,
    Screen,
    SellerInfo,
    SellerType,
)

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 9, 29)


@pytest.fixture(scope="session")
def target():
    return load_target(ROOT / "targets" / "precision-56x0.yaml")


@pytest.fixture(scope="session")
def kb(target):
    return KnowledgeBase(load_models(target.models), load_gpu_patterns())


@pytest.fixture(scope="session")
def prices(target):
    return load_prices(target.prices)


@pytest.fixture(scope="session")
def rates():
    return parse_ecb((FIXTURES / "ecb.xml").read_text())


def make_listing(
    title: str = "Dell Precision 5680 i7-13800H 32GB RTX 2000 Ada 1TB",
    price: str = "1200",
    currency: str = "EUR",
    country: str = "DE",
    source: str = "test",
    native_id: str = "1",
    **kw,
) -> RawListing:
    defaults = dict(
        delivery=Delivery.SHIPS_FR,
        protection=Protection.PLATFORM,
        seller=SellerInfo(name="seller", type=SellerType.PRIVATE, feedback_count=40),
    )
    loc = kw.pop("location", None) or Location(country=country)
    return RawListing(
        source=source,
        native_id=native_id,
        url=f"https://example.test/{source}/{native_id}",
        fetched_at=datetime(2026, 9, 29, tzinfo=UTC),
        title=title,
        price=Money(amount=Decimal(price), currency=currency),
        location=loc,
        **(defaults | kw),
    )


def make_spec(**kw) -> ExtractedSpec:
    base = dict(
        is_complete_laptop=True,
        model="Precision 5680",
        cpu="i7-13800H",
        gpu="RTX 2000 Ada",
        gpu_source=FieldSource.TITLE,
        ram_gb=32,
        ssd_gb=1000,
        screen=Screen.FHD_PLUS,
        touch=False,
        keyboard_layout=None,
        condition=Condition.USED_GOOD,
        warranty_until=None,
        warranty_months=None,
        battery_cycles=None,
        battery_health_pct=None,
        charger_included=True,
        service_tag=None,
        locked=False,
        payment_before_inspection=False,
        template_text_suspected=False,
        conflicts=[],
    )
    return ExtractedSpec(**(base | kw))


class FakeExtractor:
    """Returns canned specs keyed by listing title."""

    def __init__(self, specs: dict[str, ExtractedSpec]):
        self.specs = specs

    def extract(self, listing: RawListing) -> ExtractedSpec:
        return self.specs[listing.title]
