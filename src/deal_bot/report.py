"""Static HTML and JSON output of a run."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote_plus

from jinja2 import Environment, PackageLoader, select_autoescape

from deal_bot.pipeline import RunResult

_env = Environment(loader=PackageLoader("deal_bot"), autoescape=select_autoescape(["html", "j2"]))


def manual_links(queries: list[str]) -> list[tuple[str, str]]:
    """Prefilled searches for sources the bot cannot reach yet."""
    links = []
    for q in queries:
        enc = quote_plus(q)
        links += [
            (f"Leboncoin: {q}", f"https://www.leboncoin.fr/recherche?category=15&text={enc}"),
            (f"Back Market: {q}", f"https://www.backmarket.fr/fr-fr/search?q={enc}"),
            (f"Kleinanzeigen: {q}", f"https://www.kleinanzeigen.de/s-{q.replace(' ', '-')}/k0"),
            (f"Marktplaats: {q}", f"https://www.marktplaats.nl/q/{q.replace(' ', '+')}/"),
            (f"Vinted: {q}", f"https://www.vinted.fr/catalog?search_text={enc}"),
        ]
    return links


def write_reports(r: RunResult, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    html_path = out_dir / f"{stamp}.html"
    json_path = out_dir / f"{stamp}.json"
    html = _env.get_template("report.html.j2").render(
        r=r,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        gbp=Decimal(1) / r.rates.per_eur.get("GBP", Decimal(1)),
        manual_links=manual_links(r.target.queries),
    )
    html_path.write_text(html, encoding="utf-8")
    payload = {
        "target": r.target.slug,
        "fx_day": r.rates.day,
        "sources": r.source_status,
        "ranked": [json.loads(s.model_dump_json()) for s in r.ranked],
        "suspicious": [json.loads(s.model_dump_json()) for s in r.suspicious],
        "held": [json.loads(s.model_dump_json()) for s in r.held],
        "discarded": dict(r.rejected_tally),
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return html_path, json_path
