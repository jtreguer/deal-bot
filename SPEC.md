# deal_bot: specification

Status: draft 1, 2026-09-29. Based on the manual survey in `survey/2026-09-29.md`.

**Phase 1 progress (2026-09-29):**

- **Done:**
  - Shopify, WooCommerce and Refurbed adapters
  - FX, prefilter, extraction, validation, hard filters, landed cost, fair value, risk,
    duplicate grouping, ranking
  - HTML and JSON report
  - SQLite history
- **Waiting:**
  - eBay adapter (developer keys pending)
  - live extraction eval (needs the API key in `.env`)
- **Deferred:** EuroPC and LapStore adapters (no stock at survey time)

## 1. Goal

Find used, refurbished and open-box laptops that match a configurable target across
European marketplaces. Convert every price to what the buyer would actually pay, landed in
France with VAT included. Rank the listings by how good a deal each one is, and explain
the ranking.

The first target is the Dell Precision 5680 / 5690. Nothing in the code should be
specific to Dell or to these models; that knowledge lives in config files.

Usage has two phases. The first is an on-demand ranked report. The second adds scheduled
runs, history and alerts.

## 2. Findings that shape the design

1. **Supply is thin.** About 10 used units across the EU and UK passed the filters on
   the survey date. That changes three things:
   - A statistical fair-value model has too little data at first. Scoring starts from
     reference prices set by hand and calibrates them from history (section 7).
   - Monitoring over time is worth more than any single snapshot. Good deals are rare
     and sell quickly.
   - Every source that contributes a few listings matters.
2. **Access varies widely by source.**
   - eBay blocks plain HTTP (403 on every domain), so the official Browse API is required.
   - Leboncoin, Subito, Back Market, Cdiscount and dell.com block plain HTTP (403, Cloudflare,
     Akamai). Kleinanzeigen, Marktplaats, 2dehands, Vinted and most refurbishers do not.
   - Several refurbishers expose machine-readable catalogues: Shopify `/products.json` and
     the WooCommerce Store API.
3. **Listing data is unreliable.**
   - Titles are machine-translated and inconsistent.
   - eBay item specifics contradict the title (for example "Intel Iris Xe" on an RTX
     3500 Ada unit).
   - Sellers name GPUs that were never offered for the model (RTX 4090, "RTX A2000 Ada").
   - Spec templates are copied from other models (SODIMM RAM on a machine with soldered RAM).

   Extraction has to combine title, structured fields and description, and check the
   result against a table of configurations that actually exist.
4. **About 80% of keyword hits are not laptops.** They are parts and accessories: screens,
   batteries, palmrests, chargers.
5. **The same machine appears in several places.** The same item ID shows up on up to 9
   eBay sites. The same physical unit was also listed on eBay and Kleinanzeigen.
6. **Listings go stale.** Search engines and Vinted's own search keep showing sold or
   removed items. Sold products on refurbisher sites redirect to category pages.
7. **Shown prices depend on the viewer.** ebay.fr adds 20% VAT to US-origin prices. Some UK
   sellers show different prices depending on the viewer's location. Every price must be
   stored with its currency, site, viewer location and VAT status.

## 3. Search target (config)

```yaml
# targets/precision-56x0.yaml
name: Dell Precision 5680/5690
models: [precision-5680, precision-5690]      # keys into knowledge/models/*.yaml
hard_filters:
  ram_gb_min: 32
  gpu_exclude: [rtx-a1000, rtx-1000-ada, integrated]
  exclude_conditions: [for_parts, locked]
  must_deliver_to: FR              # or pickup in one of pickup_zones
  pickup_zones:                    # pickup-only listings kept only inside one of these
    - {name: Paris,    postcodes: ["75*"]}           # city limits only
    - {name: Bordeaux, postcodes: ["33000", "33100", "33200", "33300", "33800"]}
    - {name: Pau,      postcodes: ["64000"]}
    - {name: Biarritz, radius_km: 50}                # geocoded distance
buyer:
  country: FR
  postcode: "75001"
  vat_basis: incl                  # incl | excl (company buyer)
  keyboard_layouts: any            # recorded, not scored
budget_max_eur: null
alerts:
  min_discount_pct: 15
```

**Model knowledge base.** Each model has a file (`knowledge/models/precision-5680.yaml`)
that lists:

- valid CPUs, GPUs, RAM sizes, screens and SSD options
- whether RAM is soldered
- the release year
- aliases: `M5680`, `Precision 16 5680`, `Dell 5680`
- known Dell config codes and part-number patterns

Extraction checks its output against this file. A value outside the valid set does not
get silently corrected. The field is marked `invalid` and the listing's risk score goes up.

## 4. Architecture

A fixed pipeline runs on every execution. An LLM is used at two defined steps inside it.
There is no autonomous browsing agent in the main path.

```
collect ──► dedupe & liveness ──► prefilter ──► extract (LLM) ──► validate ──► hard filters
                                                                                     │
      report ◄── rank ◄── risk score ◄── fair value ◄── landed cost ◄────────────────┘
                   │
                   └──► investigate top N (LLM, optional) ──► report annotations
```

| Stage | Kind | What it does |
|---|---|---|
| collect | deterministic | One adapter per source. Returns `RawListing` records: source, URL, native ID, title, price, currency, shipping, seller info, structured fields, description, image URLs, and a raw HTML/JSON snapshot. |
| dedupe & liveness | deterministic | Dedupe within a source by native ID (the eBay legacy ID across all EU sites). Re-check that each listing is still live. Cross-source dedupe runs after extraction. |
| prefilter | deterministic | Cheap keyword and category rules that drop parts and accessories before any LLM spend: a multilingual blacklist (`Akku`, `Matrix`, `Klappdeckel`, `palmrest`, `carte mère`, `placa madre`, etc.), a laptop category ID where the source has one, and a price floor of about €300. |
| extract | LLM | Claude Haiku 4.5 with structured output. Input: title, structured fields and description. Output: `ExtractedSpec`. Each field records which part of the listing it came from and whether the sources disagreed. Results are cached by content hash. |
| validate | deterministic | Checks the extracted spec against the model knowledge base. Normalizes aliases. Marks impossible values and conflicts. |
| hard filters | deterministic | Applies the target's filters. Every rejected listing is kept with its reason, so the report can show a discard tally. |
| cross-source dedupe | deterministic, LLM tie-break | Candidate pairs: same model, CPU, GPU and RAM, similar price, and matching fingerprint details (for example the same aftermarket SSD). A perceptual hash of the photos confirms most pairs; an LLM decides the uncertain ones. The group keeps its most protected channel as the primary listing. |
| landed cost | deterministic | Section 6. |
| fair value | deterministic | Section 7. |
| risk score | deterministic | Section 8. |
| rank | deterministic | Section 9. |
| investigate | LLM, optional | For the top N (default 10): read the full description and photos, check claims against the knowledge base, and draft questions for the seller (GPU screenshot, battery health, service tag). Output is annotations only; it never changes the score. This is the one step where an agent loop makes sense, because the model may need to fetch the description iframe or a second page. |

### Why not a fully agentic crawler

It would cost more per run and give different results each time. It would also be hard
to debug when the ranking looks wrong. The main value of an LLM here is reading messy text
into a schema, and that doesn't need an agent loop.

A separate agentic job, `discover`, can run by hand every month or so. It searches for
new sellers that stock the target models and proposes new adapters. A human reviews the
proposals.

## 5. Sources

Access tiers:

- **A:** official API or public machine-readable feed
- **B:** plain HTTP and HTML parsing
- **C:** needs a real browser (Playwright with a persistent profile on a residential IP)
- **M:** manual; the bot generates prefilled search links and records what the user pastes back

| Source | Tier | Phase | Method / notes |
|---|---|---|---|
| eBay FR, DE, IT, ES, NL, BE, AT, IE, UK | A | 1 | Browse API `item_summary/search`, one call per marketplace (`X-EBAY-C-MARKETPLACE-ID`), laptop category, `X-EBAY-C-ENDUSERCTX: contextualLocation=country=FR,zip=75001` for shipping estimates. `getItem` returns aspects, the description and shipping options. Dedupe by legacy ID. Needs a free eBay developer account. |
| Shopify refurbishers (Cybist, Wisetek, others) | A | 1 | One generic adapter: `/products.json?limit=250`, keyword match, variant title parsed as the config, `available` flag. Adding a store only needs a config line. |
| WooCommerce refurbishers (Silicon Connect, Dubbelgaaf, Estunt) | A | 1 | One generic adapter: `/wp-json/wc/store/v1/products?search=`. |
| Refurbed (.fr .de .at .it .nl) | B | 1 | `/search/?query=`. The spec table includes the GPU. Stock and price differ by country, so query `.fr` first. |
| EuroPC, LapStore, AfB | B | 1 | One small adapter each. EuroPC uses JSON-LD; sold items redirect with a 301, so treat that as a liveness signal. |
| Kleinanzeigen | B | 2 | Plain HTML. Shipping labels are domestic only, so cross-border delivery is `unknown` unless the text says otherwise. |
| Marktplaats, 2dehands | B | 2 | Same platform. Search results mix in other models, so filter on the title. Possibly `__NEXT_DATA__` JSON (unconfirmed). |
| Vinted | B | 2 | Server-rendered. Must detect removed items ("Enlevé"). Shown prices include buyer-protection fees. |
| Amazon .fr .de | B/C | 2 | Product pages work; search is intermittently blocked. Track a fixed list of product IDs (ASINs) and check their used and Renewed offers. |
| Leboncoin | C / M | 2 | 403 on plain HTTP. Try Playwright with a persistent, logged-out profile from the home IP, at low frequency (a few requests per run). If that fails, fall back to M. It is the most valuable classifieds source for a buyer in France, because pickup is realistic. |
| Back Market | C / M | 2 | Cloudflare. Titles leave out the GPU, so every listing needs its product page. Same approach as Leboncoin. |
| Subito, Wallapop, willhaben | M | later | Low yield in the survey. Links only. |
| Dell outlet (any EU country) | none | | No EU refurbished store sells these models to France. Check again once a year. |
| Reference: new price | A | 1 | high-end-systems' eBay store (new built-to-order units) as the price ceiling. Optionally idealo or geizhals. |
| FX rates | A | 1 | ECB daily XML `eurofxref-daily.xml`, cached daily. |

**Adapter contract:** `search(target) -> Iterable[RawListing]` and
`refresh(listing) -> Liveness`.

- Each adapter declares its tier, its rate limit and whether it may run unattended.
- A failing adapter logs the error and records its status in the report.
- It never stops the run.

**Politeness:**

- Per-domain rate limits, one request every 5 to 10 seconds by default on tier B.
- A real user agent string.
- robots.txt is respected on tier B.
- HTML snapshots are cached and never re-fetched within a run.
- The Google Translate proxy trick used in the survey is not used in production.

## 6. Landed cost

All amounts are in EUR, VAT included, delivered to the buyer's postcode.

| Seller location / type | Landed cost |
|---|---|
| EU seller, any type | price + shipping to FR (+ platform buyer-protection fee if any) |
| EU seller, pickup only | price. The distance to the nearest pickup zone is stored; the listing is dropped if it falls outside every zone |
| UK / CH, eBay International Shipping | price + shipping + eBay's import charges if the API returns them, otherwise 20% × (price + shipping) |
| UK / CH, direct shipping | FX(price ex-UK-VAT if the seller zero-rates exports, else price incl) + shipping + 20% FR import VAT + €20 carrier fee. Customs duty 0% (8471.30) |
| Company buyer (`vat_basis: excl`) | Same rules on ex-VAT amounts. Margin-scheme sellers cannot deduct VAT, so this is shown in the report |

Every result records how it was computed and marks estimated parts, for example
`import_vat=estimated`.

Optional adjustments are shown in the report but kept out of the landed figure:

- expected SSD upgrade cost when the SSD is below a target size
- charger replacement if no charger is included (about €60 for the 165 W USB-C)

## 7. Fair value

The expected price is built up additively from configurable parts:

```
fair_value = base[model]
           + gpu_delta[gpu] + cpu_delta[cpu] + ram_delta[ram]
           + screen_delta[screen] + ssd_per_tb × ssd_tb
           + condition_delta[condition]
           + warranty_per_month × warranty_months_remaining
```

**Initial values** (starting guesses from the survey, all in `knowledge/prices/*.yaml`):

- base 5680 with 32 GB, RTX 2000 Ada, i7, FHD+, 1 TB, good used: €1,250
- 5690: +€100
- RTX 3500 Ada: +€550
- RTX 5000 Ada: +€1,000
- i9: +€80
- 64 GB: +€200
- OLED touch: +€150 (confirmed as a plus by the buyer)
- per TB of SSD: €60
- conditions: new +€900, open box +€500, refurbished grade A +€100, fair −€100
- remaining Dell warranty: €15 per month

**Calibration (phase 3):**

- Once 50 or more listings are in history, fit the deltas with ridge regression, using
  the hand-set values as the prior.
- Compare fitted and hand-set values in the report. A human decides whether to adopt them.
- Listings that sold at a known price count as stronger evidence than asking prices.
  For eBay, this means ended listings the adapter re-checks.

`discount = (fair_value − landed_cost) / fair_value`

## 8. Risk score

The risk score is kept apart from price. A cheap listing with a high risk score must
still look risky in the report. The score runs from 0 to 100, and each rule adds points
and a reason string.

| Signal | Points |
|---|---|
| Seller has 0 feedback or an account under 3 months old | +25 |
| Discount over 40% relative to fair value | +25 (over 55%: +40) |
| Impossible spec for the model (knowledge base check) | +20 |
| Title, structured fields and description disagree on GPU, RAM or CPU | +15 |
| GPU not stated | hard-filter hold: listed separately as "needs confirmation" |
| No buyer protection (bank transfer, private cross-border sale with no platform checkout) | +20 |
| Payment requested before inspection ("sealed, test after payment") | +20 |
| Copy-paste template text (description matches another seller or another model) | +10 |
| Stock photos only (reverse lookup against the manufacturer's images) | +10 |
| Auction with no bids ending soon | 0; shown as an info note |

## 9. Ranking

Default ordering:

1. Drop listings that fail a hard filter.
2. Put listings with risk ≥ 60 into a separate "suspicious" section.
3. Sort the rest by `adjusted_discount = discount − 0.004 × risk − protection_penalty`.
   `protection_penalty` is 0 for eBay or a retailer, 0.03 for pickup, and 0.08 for an
   unprotected transfer.

The weights live in config. Each row shows its full calculation, so any ranking can be
traced back to numbers.

## 10. Report

The report is one static HTML file, `reports/<target>/<date>.html`, generated with Jinja2
and needing no server.

**Header:**

- run time
- per-source status (listings found, errors, blocked)
- FX rate used
- config tier price bands (the "market read")

**Ranked table** (one row per listing):

- rank
- landed € (estimated parts marked)
- fair value, discount
- risk score and reasons
- model, CPU, RAM, GPU, SSD, screen, layout
- condition, warranty, battery
- seller and feedback
- protection
- location (distance for pickup)
- auction end time
- link
- other channels for the same unit (from cross-source dedupe)

**Other sections:**

- needs confirmation (GPU missing)
- suspicious
- manual sources to check, with prefilled search URLs
- discard tally by reason
- investigation notes for the top N

**Also exported:** CSV/JSON of the same data.

## 11. Data model

The pydantic models live in `src/deal_bot/models.py`:

- `RawListing` is what an adapter returns.
- `ExtractedSpec` is the LLM output schema.
- `Scored` carries a listing through validation, filters, pricing and scoring.

**Storage:** SQLite (`data/deal_bot.sqlite`), with tables `runs`, `listings`,
`observations` (landed price, fair value, verdict and risk per run) and `extractions`
(cache keyed by content hash).

## 12. Stack

- **Language and tooling:** Python 3.12+, uv, Typer CLI
- **Fetching and parsing:** httpx, selectolax, Playwright (tier C only)
- **Data:** pydantic v2, SQLite
- **Report:** Jinja2
- **Fair-value calibration:** scikit-learn (phase 3 only)
- **LLM:** the `anthropic` SDK with `claude-haiku-4-5` for extraction. A stronger model can
  be configured for the optional investigate step.
- **Secrets:** API keys (eBay, Anthropic) come from the environment or `.env`; `.env` is
  never committed.
- **Runtime:** local first (cron or a systemd timer), with nothing that prevents a later
  move. Tier C adapters need a residential IP, so a VPS move would require either dropping
  them or running them locally. That is an open question below.

## 13. Phases and acceptance criteria

**Phase 1: one-shot report**

- **Scope:**
  - eBay adapter (9 marketplaces)
  - generic Shopify and WooCommerce adapters
  - Refurbed, EuroPC, LapStore adapters
  - FX, extraction, validation, landed cost, fair value, risk, HTML report
- **Acceptance:**
  - Given the listings in `survey/2026-09-29.md` as fixtures, extraction gets model, RAM,
    GPU and CPU right on ≥95% of them.
  - Every impossible GPU claim from the survey is marked.
  - The 318922405121 auction and the Kleinanzeigen listing 3491938688 are grouped as one unit.
  - A full live run finishes in under 10 minutes and costs under €0.50 in LLM calls.

**Phase 2: classifieds and blocked sources**

- **Scope:**
  - Kleinanzeigen, Marktplaats / 2dehands, Vinted, Amazon offers
  - Leboncoin and Back Market through Playwright, with the manual fallback
  - cross-source dedupe with photo hashes
  - the investigate step
- **Acceptance:** a blocked source degrades to manual links without failing the run.

**Phase 3: monitoring**

- **Scope:**
  - scheduled runs
  - history and price trends per config tier
  - fair-value calibration
  - alerts for new listings with `adjusted_discount` ≥ threshold and risk < 40, and for
    auctions ending within 24 h that are below fair value
- **Alert channel:** ntfy.

## 14. Open questions

1. **eBay developer account.** Requested on 2026-09-29; approval expected in about one
   business day.
2. **Tier C sources.** Is running Playwright from your own machine at low frequency
   acceptable for Leboncoin and Back Market, given their terms of service? Or should they
   stay manual?
3. **Budget ceiling.** Currently none.

Decided on 2026-09-29:

- OLED touch counts as a plus.
- Pickup zones are Paris, Bordeaux and Pau (inside city limits) and Biarritz (50 km).
- Leboncoin and Back Market run through Playwright on the user's machine, with a
  manual fallback.
- Phase 3 alerts go through ntfy.

## 15. Out of scope

- Buying, bidding or messaging sellers automatically. The bot drafts questions; you send them.
- Logging in to any marketplace.
- Getting around captchas or bot protection with third-party solving services.
