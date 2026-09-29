# deal_bot

Finds used and refurbished laptops that match a target configuration, converts every price
to the landed cost in France (VAT included), and ranks the listings by discount to an
estimated fair value, with a separate risk score. Design and rationale are in `SPEC.md`;
the manual market survey that shaped it is in `survey/`.

## Setup

    uv sync
    cp .env.example .env    # then set ANTHROPIC_API_KEY

## Use

    uv run deal-bot run targets/precision-56x0.yaml       # full run, writes reports/<target>/<date>.html
    uv run deal-bot collect targets/precision-56x0.yaml   # adapters + prefilter only, no LLM calls
    uv run deal-bot run targets/precision-56x0.yaml --only cybist --only refurbed

## Configuration

| File | What it holds |
|---|---|
| `targets/*.yaml` | Models, hard filters, buyer location, pickup zones, ranking weights |
| `knowledge/models/*.yaml` | Valid CPU/GPU/RAM/screen options per model, name aliases |
| `knowledge/gpus.yaml` | GPU name normalization (ordered regexes) |
| `knowledge/prices/*.yaml` | Fair-value base prices and deltas |
| `sources.yaml` | Retail stores per adapter, with shipping and VAT assumptions |

## Tests

    uv run pytest               # offline tests
    uv run pytest -m live -s    # extraction accuracy on survey titles; calls the Claude API
    uv run ruff check . && uv run ruff format --check .
