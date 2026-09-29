"""Additive fair-value model: base price for the model plus deltas per spec."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from deal_bot.config import PriceModel
from deal_bot.models import ExtractedSpec, FairValue, Screen, Validation


def warranty_months_left(spec: ExtractedSpec, today: date) -> int:
    if end := spec.warranty_until:
        months = (end.year - today.year) * 12 + end.month - today.month - (end.day < today.day)
        return max(0, months)
    return spec.warranty_months or 0


def fair_value(spec: ExtractedSpec, v: Validation, prices: PriceModel, today: date) -> FairValue | None:
    if v.model_key not in prices.base:
        return None
    ref = prices.reference
    parts: list[tuple[str, Decimal]] = [(f"base {v.model_key}", prices.base[v.model_key])]
    assumptions: list[str] = []

    def add(label: str, table: dict, key, ref_key: str) -> None:
        if key is None or key not in table:
            assumptions.append(f"{label} unknown, assumed {ref[ref_key]}")
            return
        if delta := table[key]:
            parts.append((f"{label} {key}", delta))

    add("GPU", prices.gpu, v.gpu_key, "gpu")
    add("CPU", prices.cpu_class, v.cpu_class, "cpu_class")
    add("RAM", prices.ram_gb, spec.ram_gb, "ram_gb")
    screen = None if spec.screen in (Screen.UNKNOWN, Screen.OTHER) else spec.screen.value
    add("screen", prices.screen, screen, "screen")
    add("condition", prices.condition, spec.condition.value, "condition")

    if spec.ssd_gb:
        tb_diff = Decimal(spec.ssd_gb - int(ref["ssd_gb"])) / 1000
        if tb_diff:
            parts.append((f"SSD {spec.ssd_gb} GB", (prices.ssd_per_tb * tb_diff).quantize(Decimal(1))))
    else:
        assumptions.append(f"SSD unknown, assumed {ref['ssd_gb']} GB")

    if months := warranty_months_left(spec, today):
        parts.append((f"warranty {months} months left", prices.warranty_per_month * months))

    return FairValue(value_eur=sum((d for _, d in parts), Decimal(0)), parts=parts, assumptions=assumptions)
