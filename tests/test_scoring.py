from datetime import date
from decimal import Decimal

from conftest import TODAY, make_listing, make_spec

from deal_bot.models import LandedCost, Protection, Scored, SellerInfo, SellerType, Verdict
from deal_bot.scoring import group_duplicates, rank, risk


def _scored(kb, spec=None, discount=0.1, landed="1200", **kw):
    spec = spec or make_spec()
    s = Scored(raw=make_listing(**kw), spec=spec, discount=discount)
    s.validation = kb.validate(spec, s.raw.title)
    s.landed = LandedCost(total_eur=Decimal(landed), parts=[("price", Decimal(landed), False)])
    return s


def test_clean_listing_has_no_risk(kb):
    s = _scored(kb)
    risk(s, TODAY)
    assert (s.risk, s.risk_reasons) == (0, [])


def test_zero_feedback_and_too_cheap(kb):
    s = _scored(kb, discount=0.6, seller=SellerInfo(type=SellerType.PRIVATE, feedback_count=0))
    risk(s, TODAY)
    assert s.risk == 65


def test_new_account_no_protection_impossible_gpu(kb):
    s = _scored(
        kb,
        spec=make_spec(gpu="RTX 4090"),
        protection=Protection.NONE,
        seller=SellerInfo(type=SellerType.PRIVATE, feedback_count=3, member_since=date(2026, 9, 1)),
    )
    risk(s, TODAY)
    assert s.risk == 25 + 20 + 20


def test_rank_orders_by_adjusted_discount_and_splits_suspicious(kb, target):
    a = _scored(kb, discount=0.10, native_id="a")
    b = _scored(kb, discount=0.20, native_id="b", protection=Protection.PICKUP)
    c = _scored(kb, discount=0.60, native_id="c", seller=SellerInfo(type=SellerType.PRIVATE, feedback_count=0))
    d = _scored(kb, discount=0.30, native_id="d")
    d.verdict = Verdict.HOLD
    for s in (a, b, c, d):
        risk(s, TODAY)
    ranked, suspicious = rank([a, b, c, d], target.ranking)
    assert [s.raw.native_id for s in ranked] == ["b", "a"]
    assert [s.raw.native_id for s in suspicious] == ["c"]
    assert abs(b.adjusted_discount - (0.20 - 0.03)) < 1e-9


def test_cross_listed_private_unit_is_grouped(kb):
    # Survey: the same 5680 on an eBay DE auction and on Kleinanzeigen.
    ebay = _scored(kb, source="ebay-de", native_id="318922405121", landed="1117.49")
    ka = _scored(kb, source="kleinanzeigen", native_id="3491938688", landed="1245", protection=Protection.NONE)
    group_duplicates([ebay, ka])
    assert ka.duplicate_of == ebay.raw.key
    assert ebay.duplicates == [ka.raw.key]


def test_two_shops_with_same_config_are_not_grouped(kb):
    shop = SellerInfo(type=SellerType.BUSINESS)
    a = _scored(kb, source="shop-a", seller=shop, protection=Protection.RETAILER)
    b = _scored(kb, source="shop-b", seller=shop, protection=Protection.RETAILER)
    group_duplicates([a, b])
    assert a.duplicate_of is None and b.duplicate_of is None


def test_cross_listed_unit_grouped_when_one_side_omits_ssd(kb):
    ebay = _scored(kb, source="ebay-de", native_id="a", landed="1117.49")
    ka = _scored(kb, spec=make_spec(ssd_gb=None), source="kleinanzeigen", native_id="b", landed="1245")
    group_duplicates([ebay, ka])
    assert ka.duplicate_of == ebay.raw.key


def test_different_ssd_sizes_are_different_units(kb):
    a = _scored(kb, source="ebay-de", native_id="a")
    b = _scored(kb, spec=make_spec(ssd_gb=2000), source="kleinanzeigen", native_id="b")
    group_duplicates([a, b])
    assert b.duplicate_of is None
