"""Extraction accuracy against the survey titles. Calls the Claude API: `uv run pytest -m live`."""

import pytest
import yaml
from conftest import FIXTURES, make_listing
from dotenv import load_dotenv

from deal_bot.config import ROOT
from deal_bot.extract import ClaudeExtractor
from deal_bot.store import Store

load_dotenv(ROOT / ".env")
CASES = yaml.safe_load((FIXTURES / "extraction_cases.yaml").read_text())


def _actual(kb, spec, title, field):
    v = kb.validate(spec, title)
    return {
        "model": v.model_key,
        "cpu": v.cpu_key,
        "gpu": v.gpu_key,
        "ram_gb": spec.ram_gb,
        "ssd_gb": spec.ssd_gb,
        "screen": spec.screen.value,
        "condition": spec.condition.value,
        "is_complete_laptop": spec.is_complete_laptop,
    }[field]


@pytest.mark.live
def test_extraction_accuracy(kb, tmp_path):
    extractor = ClaudeExtractor(Store(tmp_path / "cache.sqlite"))
    total = correct = 0
    misses = []
    for i, case in enumerate(CASES):
        listing = make_listing(title=case["title"], native_id=str(i), structured=case.get("structured", {}))
        spec = extractor.extract(listing)
        for field, want in case["expect"].items():
            got = _actual(kb, spec, case["title"], field)
            total += 1
            if got == want:
                correct += 1
            else:
                misses.append(f"{case['title'][:60]!r} {field}: got {got!r}, want {want!r}")
    accuracy = correct / total
    print(f"\n{correct}/{total} fields correct ({accuracy:.1%})", *misses, sep="\n")
    assert accuracy >= 0.95, "\n".join(misses)
