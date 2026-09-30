"""Command-line entry point: `deal-bot run targets/precision-56x0.yaml`."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

from deal_bot.adapters.base import Adapter, Http
from deal_bot.adapters.ebay import EbayAdapter
from deal_bot.adapters.refurbed import RefurbedAdapter
from deal_bot.adapters.shops import ShopConfig, ShopifyAdapter, WooCommerceAdapter
from deal_bot.config import ROOT, load_gpu_patterns, load_models, load_prices, load_sources, load_target
from deal_bot.extract import DEFAULT_MODEL, ClaudeExtractor
from deal_bot.filters import prefilter
from deal_bot.fx import load_rates
from deal_bot.knowledge import KnowledgeBase
from deal_bot.pipeline import collect as collect_listings
from deal_bot.pipeline import run as run_pipeline
from deal_bot.report import write_reports
from deal_bot.store import Store

app = typer.Typer(add_completion=False, no_args_is_help=True)
DATA = ROOT / "data"
load_dotenv(ROOT / ".env")


def build_adapters(sources: dict, only: set[str] | None) -> list[Adapter]:
    adapters: list[Adapter] = [ShopifyAdapter(ShopConfig(**c)) for c in sources.get("shopify", [])]
    adapters += [WooCommerceAdapter(ShopConfig(**c)) for c in sources.get("woocommerce", [])]
    if rb := sources.get("refurbed"):
        adapters.append(RefurbedAdapter(rb["domains"]))
    if eb := sources.get("ebay"):
        adapters.append(EbayAdapter(eb["marketplaces"], eb["category_ids"]))
    return [a for a in adapters if not only or a.name in only]


@app.command()
def run(
    target_file: Path,
    sources_file: Annotated[Path, typer.Option("--sources")] = ROOT / "sources.yaml",
    only: Annotated[list[str] | None, typer.Option("--only", help="Run just these sources")] = None,
    model: Annotated[str, typer.Option(help="Claude model for extraction")] = DEFAULT_MODEL,
    interval: Annotated[float, typer.Option(help="Seconds between requests to one host")] = 5.0,
    verbose: Annotated[bool, typer.Option("-v")] = False,
) -> None:
    """Collect, score and rank listings; write an HTML and a JSON report."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(message)s")
    target = load_target(target_file)
    kb = KnowledgeBase(load_models(target.models), load_gpu_patterns())
    store = Store(DATA / "deal_bot.sqlite")
    http = Http(min_interval=interval)
    rates = load_rates(DATA / "fx", http.client)
    adapters = build_adapters(load_sources(sources_file), set(only) if only else None)

    result = run_pipeline(target, adapters, kb, load_prices(target.prices), rates, ClaudeExtractor(store, model), http)
    store.record_run(target.slug, result.source_status, result.items)
    html, md, _ = write_reports(result, ROOT / "reports" / target.slug)

    for name, status in result.source_status.items():
        typer.echo(f"{name:20} {status}")
    typer.echo(f"\n{len(result.ranked)} ranked, {len(result.suspicious)} suspicious, {len(result.held)} to confirm")
    for i, s in enumerate(result.ranked[:10], 1):
        flag = "⚠" if s.validation.invalid else " "
        typer.echo(f"{i:2}. {s.landed.total_eur:>8} EUR  {s.discount:+.0%}  risk {s.risk:>3} {flag} {s.raw.title[:70]}")
    typer.echo(f"\nReport: {html}\n        {md}")


@app.command()
def collect(
    target_file: Path,
    sources_file: Annotated[Path, typer.Option("--sources")] = ROOT / "sources.yaml",
    only: Annotated[list[str] | None, typer.Option("--only", help="Run just these sources")] = None,
    interval: Annotated[float, typer.Option(help="Seconds between requests to one host")] = 5.0,
) -> None:
    """Fetch listings and apply the prefilter only. No LLM calls; for checking adapters."""
    target = load_target(target_file)
    kb = KnowledgeBase(load_models(target.models), load_gpu_patterns())
    http = Http(min_interval=interval)
    rates = load_rates(DATA / "fx", http.client)
    adapters = build_adapters(load_sources(sources_file), set(only) if only else None)
    listings, status = collect_listings(adapters, target, http)
    for name, st in status.items():
        typer.echo(f"{name:20} {st}")
    for x in listings:
        reason = prefilter(x, kb, target, rates)
        mark = "keep" if reason is None else f"drop ({reason})"
        typer.echo(f"{x.source:16} {x.price.amount:>9} {x.price.currency}  {mark:32} {x.title[:80]}")


if __name__ == "__main__":
    app()
