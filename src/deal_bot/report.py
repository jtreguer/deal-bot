"""Static HTML, Markdown and JSON output of a run."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote_plus

from jinja2 import Environment, PackageLoader, select_autoescape

from deal_bot.models import Scored
from deal_bot.pipeline import RunResult

_env = Environment(loader=PackageLoader("deal_bot"), autoescape=select_autoescape(["html.j2"]))


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


def _dump(s: Scored) -> dict:
    # Descriptions are raw seller HTML, often 100 kB each; they stay in SQLite, not in the report.
    return json.loads(s.model_dump_json(exclude={"raw": {"description"}}))


def write_reports(r: RunResult, out_dir: Path) -> tuple[Path, Path, Path]:
    """Write the HTML, Markdown and JSON reports; return their paths in that order."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    html_path, md_path, json_path = (out_dir / f"{stamp}.{ext}" for ext in ("html", "md", "json"))
    context = dict(
        r=r,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        gbp=Decimal(1) / r.rates.per_eur.get("GBP", Decimal(1)),
        manual_links=manual_links(r.target.queries),
    )
    html = _env.get_template("report.html.j2").render(**context)
    md = _env.get_template("report.md.j2").render(**context)
    payload = {
        "target": r.target.slug,
        "fx_day": r.rates.day,
        "sources": r.source_status,
        "ranked": [_dump(s) for s in r.ranked],
        "suspicious": [_dump(s) for s in r.suspicious],
        "held": [_dump(s) for s in r.held],
        "discarded": dict(r.rejected_tally),
    }
    js = json.dumps(payload, indent=2, ensure_ascii=False)
    # Timestamped copies keep the history; latest.* is what to open.
    for name, text in ((html_path, html), (md_path, md), (json_path, js)):
        name.write_text(text, encoding="utf-8")
        (out_dir / f"latest{name.suffix}").write_text(text, encoding="utf-8")
    return html_path, md_path, json_path
