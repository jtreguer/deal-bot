"""ECB daily reference rates, cached per day on disk."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_NS = {"e": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}


@dataclass(frozen=True)
class Rates:
    day: str
    per_eur: dict[str, Decimal]  # units of currency per 1 EUR, as the ECB publishes them

    def to_eur(self, amount: Decimal, currency: str) -> Decimal:
        if currency == "EUR":
            return amount
        try:
            return amount / self.per_eur[currency]
        except KeyError:
            raise ValueError(f"no ECB rate for {currency}") from None


def parse_ecb(xml_text: str) -> Rates:
    root = ET.fromstring(xml_text)
    day_cube = root.find(".//e:Cube[@time]", _NS)
    if day_cube is None:
        raise ValueError("unexpected ECB XML layout")
    rates = {c.get("currency"): Decimal(c.get("rate")) for c in day_cube.findall("e:Cube", _NS)}
    return Rates(day=day_cube.get("time"), per_eur=rates)


def load_rates(cache_dir: Path, client: httpx.Client | None = None) -> Rates:
    cache = cache_dir / f"ecb-{date.today().isoformat()}.json"
    if cache.exists():
        data = json.loads(cache.read_text())
        return Rates(day=data["day"], per_eur={k: Decimal(v) for k, v in data["per_eur"].items()})
    resp = (client or httpx).get(ECB_URL, timeout=20)
    resp.raise_for_status()
    rates = parse_ecb(resp.text)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"day": rates.day, "per_eur": {k: str(v) for k, v in rates.per_eur.items()}}))
    return rates
