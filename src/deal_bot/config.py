"""Loading of target, knowledge, price and source files."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = ROOT / "knowledge"


class HardFilters(BaseModel):
    ram_gb_min: int = 0
    gpu_exclude: list[str] = Field(default_factory=list)
    exclude_conditions: list[str] = Field(default_factory=list)
    price_floor_eur: Decimal = Decimal(0)
    # Listed price in EUR, before shipping and import charges. None = no ceiling.
    price_ceiling_eur: Decimal | None = None


class Buyer(BaseModel):
    country: str = "FR"
    postcode: str
    vat_basis: str = "incl"

    @model_validator(mode="after")
    def _only_incl(self) -> Buyer:
        if self.vat_basis != "incl":
            raise ValueError("only vat_basis: incl is implemented")
        return self


class PickupZone(BaseModel):
    name: str
    postcodes: list[str] = Field(default_factory=list)  # fnmatch patterns
    lat: float | None = None
    lon: float | None = None
    radius_km: float | None = None


class Ranking(BaseModel):
    risk_weight: float = 0.004
    suspicious_risk: int = 60
    protection_penalty: dict[str, float] = Field(default_factory=dict)


class Target(BaseModel):
    name: str
    slug: str
    models: list[str]
    prices: str | list[str]  # price files; a listing uses the one whose `base` has its model
    queries: list[str]
    hard_filters: HardFilters
    buyer: Buyer
    pickup_zones: list[PickupZone] = Field(default_factory=list)
    ranking: Ranking = Field(default_factory=Ranking)


class ModelInfo(BaseModel):
    key: str
    label: str
    brand: str
    release_year: int
    aliases: list[str]
    ram_soldered: bool
    ram_gb: list[int]
    cpus: list[str]
    gpus: list[str]
    screens: list[str]
    screen_inches: float


class GpuPattern(BaseModel):
    key: str
    pattern: str
    warn: str | None = None


class PriceModel(BaseModel):
    base: dict[str, Decimal]
    reference: dict[str, str | int]
    gpu: dict[str, Decimal]
    cpu_class: dict[str, Decimal]
    ram_gb: dict[int, Decimal]
    screen: dict[str, Decimal]
    ssd_per_tb: Decimal
    condition: dict[str, Decimal]
    warranty_per_month: Decimal


def _load(path: Path):
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_target(path: Path) -> Target:
    return Target.model_validate(_load(path))


def load_models(keys: list[str]) -> dict[str, ModelInfo]:
    return {k: ModelInfo.model_validate(_load(KNOWLEDGE / "models" / f"{k}.yaml")) for k in keys}


def load_gpu_patterns() -> list[GpuPattern]:
    return [GpuPattern.model_validate(p) for p in _load(KNOWLEDGE / "gpus.yaml")]


def load_prices(names: str | list[str]) -> list[PriceModel]:
    names = [names] if isinstance(names, str) else names
    return [PriceModel.model_validate(_load(KNOWLEDGE / "prices" / f"{n}.yaml")) for n in names]


def load_sources(path: Path) -> dict:
    return _load(path)
