from decimal import Decimal

from conftest import TODAY, FakeExtractor, make_listing, make_spec

from deal_bot.models import (
    Condition,
    Delivery,
    LandedCost,
    Location,
    Money,
    Protection,
    Scored,
    Screen,
    SellerInfo,
    SellerType,
)
from deal_bot.pipeline import RunResult, score_listings
from deal_bot.report import write_reports
from deal_bot.scoring import rank

# A slice of the 2026-09-29 survey, with the specs the extractor should produce.
SURVEY = [
    (
        make_listing(
            title="Dell Precision 5680 | i7-13800H | 32 Go DDR5 | RTX 2000 Ada (8 Go) | 1 To Lexar NM790",
            price="1100",
            source="ebay-de",
            native_id="318922405121",
            shipping=Money(amount=Decimal("17.49"), currency="EUR"),
            is_auction=True,
            bids=0,
            seller=SellerInfo(name="enayc_0", type=SellerType.PRIVATE, feedback_count=0),
        ),
        make_spec(),
    ),
    (
        make_listing(
            title="Dell Precision 5680 i7-13800H 32GB RTX 2000 Ada 1 TB Lexar NM790",
            price="1225",
            source="kleinanzeigen",
            native_id="3491938688",
            protection=Protection.NONE,
            shipping=Money(amount=Decimal(20), currency="EUR"),
        ),
        make_spec(),
    ),
    (
        make_listing(
            title='Dell Precision 5680 X2W33 16" UHD+ OLED tactile i9-13900H 32 Go 1 To 3500 ADA W11P',
            price="2000",
            source="ebay-de",
            native_id="398090174263",
            shipping=Money(amount=Decimal("22.49"), currency="EUR"),
        ),
        make_spec(cpu="i9-13900H", gpu="3500 ADA", screen=Screen.UHD_PLUS_OLED, condition=Condition.USED_LIKE_NEW),
    ),
    (
        make_listing(
            title="Dell Precision 5690 Ultra 7 32GB 1TB RTX 2000 Ada",
            price="1250",
            source="marktplaats",
            native_id="m2440864972",
            delivery=Delivery.PICKUP,
            location=Location(country="NL", city="Varsselder"),
        ),
        make_spec(model="Precision 5690", cpu="Core Ultra 7"),
    ),
    (
        make_listing(
            title='Dell Precision 5680 16" Touch 4K+ OLED i9-13900H 64 Go 1 To RTX 4090 NEUF',
            price="3999.90",
            source="ebay-de",
            native_id="276452528684",
        ),
        make_spec(cpu="i9-13900H", gpu="RTX 4090", ram_gb=64, condition=Condition.OPEN_BOX),
    ),
    (
        make_listing(
            title='Dell Precision 5680 i7-13800H 32GB 16"',
            price="650",
            source="kleinanzeigen",
            native_id="karlsruhe",
            delivery=Delivery.PICKUP,
            location=Location(country="DE", city="Karlsruhe"),
        ),
        make_spec(gpu=None, gpu_source="not_found"),
    ),
    (
        make_listing(title="Dell Precision 5690 Palmrest UK Backlit Layout C9FMN", price="120", source="shop"),
        None,  # never reaches the extractor
    ),
]


def _run(target, kb, prices, rates):
    extractor = FakeExtractor({listing.title: spec for listing, spec in SURVEY if spec})
    items, prefiltered = score_listings([x for x, _ in SURVEY], target, kb, prices, rates, extractor, TODAY)
    ranked, suspicious = rank(items, target.ranking)
    return RunResult(target, rates, TODAY, {"test": "ok"}, prefiltered, items, ranked, suspicious)


def test_survey_slice_end_to_end(target, kb, prices, rates, tmp_path):
    r = _run(target, kb, prices, rates)
    by_id = {s.raw.native_id: s for s in r.items}

    assert r.prefiltered == {"part or accessory": 1}
    assert by_id["3491938688"].duplicate_of == "ebay-de:318922405121"
    assert by_id["m2440864972"].verdict_reasons == ["pickup only, outside the pickup zones"]
    assert by_id["karlsruhe"].verdict.value == "reject"  # pickup outside zones beats the GPU hold
    assert any("never offered" in x for x in by_id["276452528684"].risk_reasons)

    ranked_ids = [s.raw.native_id for s in r.ranked]
    assert "318922405121" in ranked_ids and "398090174263" in ranked_ids
    assert "3491938688" not in ranked_ids

    html, md, js = write_reports(r, tmp_path)
    text = html.read_text()
    assert "318922405121" in text and "Leboncoin" in text
    assert js.exists() and '"description"' not in js.read_text()
    assert (tmp_path / "latest.md").read_text() == md.read_text()
    table = md.read_text()
    assert "318922405121" in table and "Leboncoin" in table
    assert "&#" not in table and "&amp;" not in table  # Markdown is not HTML-escaped
    rows = [line for line in table.splitlines() if line.startswith("| ")]
    assert len({line.count(" | ") for line in rows[: len(r.ranked) + 1]}) == 1  # titles cannot break the table


def test_market_bands_leave_out_impossible_configs(kb, target, rates):
    def item(gpu, price):
        listing = make_listing()
        spec = make_spec(gpu=gpu)
        landed = LandedCost(total_eur=price, parts=[])
        return Scored(raw=listing, spec=spec, validation=kb.validate(spec, ""), landed=landed)

    r = RunResult(target, rates, TODAY, items=[item("RTX 2000 Ada", 2000), item("RTX 4090", 4000)])
    assert r.market_bands() == [("rtx-2000-ada", 1, 2000.0, 2000.0)]
