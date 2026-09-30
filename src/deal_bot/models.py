"""Data shapes shared by every pipeline stage."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class Condition(StrEnum):
    NEW = "new"
    OPEN_BOX = "open_box"
    REFURB_EXCELLENT = "refurb_excellent"
    REFURB_GOOD = "refurb_good"
    REFURB_FAIR = "refurb_fair"
    USED_LIKE_NEW = "used_like_new"
    USED_GOOD = "used_good"
    USED_FAIR = "used_fair"
    FOR_PARTS = "for_parts"
    UNKNOWN = "unknown"


class Screen(StrEnum):
    FHD_PLUS = "fhd_plus"
    UHD_PLUS_OLED = "uhd_plus_oled"
    OTHER = "other"
    UNKNOWN = "unknown"


class Delivery(StrEnum):
    SHIPS_FR = "ships_fr"
    PICKUP = "pickup"
    NO_FR = "no_fr"
    UNKNOWN = "unknown"


class Protection(StrEnum):
    PLATFORM = "platform"  # eBay money-back guarantee, Vinted protection, ...
    RETAILER = "retailer"  # EU consumer law, retailer warranty
    PICKUP = "pickup"  # inspect before paying
    NONE = "none"  # bank transfer to a private seller


class SellerType(StrEnum):
    PRIVATE = "private"
    BUSINESS = "business"
    UNKNOWN = "unknown"


class Money(BaseModel):
    amount: Decimal
    currency: str  # ISO 4217
    vat_included: bool | None = None  # None = unknown
    viewer_location: str | None = None  # e.g. "FR-75001"; shown prices can depend on it


class Location(BaseModel):
    country: str  # ISO 3166 alpha-2
    city: str | None = None
    postcode: str | None = None
    lat: float | None = None
    lon: float | None = None


class SellerInfo(BaseModel):
    name: str | None = None
    type: SellerType = SellerType.UNKNOWN
    feedback_count: int | None = None
    feedback_pct: float | None = None
    member_since: date | None = None


class SourcePolicy(BaseModel):
    """Per-source facts the landed-cost step needs and that listings do not carry."""

    price_includes_local_vat: bool = True
    export_zero_rated: str = "unknown"  # yes | no | unknown
    shipping_estimate_eur: Decimal | None = None
    via_ebay_international_shipping: bool = False
    carrier: str | None = None  # dhl | ups | fedex | postal; None = unknown, for import clearance fees


class RawListing(BaseModel):
    source: str
    native_id: str
    url: str
    fetched_at: datetime
    title: str
    price: Money
    shipping: Money | None = None  # None = not stated
    import_charges: Money | None = None  # when the platform quotes them
    delivery: Delivery = Delivery.UNKNOWN
    protection: Protection = Protection.NONE
    location: Location
    seller: SellerInfo = Field(default_factory=SellerInfo)
    structured: dict[str, str] = Field(default_factory=dict)
    description: str | None = None
    images: list[str] = Field(default_factory=list)
    is_auction: bool = False
    bids: int | None = None
    auction_ends_at: datetime | None = None
    available: bool = True
    policy: SourcePolicy = Field(default_factory=SourcePolicy)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.native_id}"


# --- LLM extraction output. Kept flat and simple: it is sent as a JSON schema. ---


class FieldSource(StrEnum):
    TITLE = "title"
    STRUCTURED = "structured"
    DESCRIPTION = "description"
    NOT_FOUND = "not_found"


class ExtractedSpec(BaseModel):
    is_complete_laptop: bool = Field(
        description="False when the item is a part, accessory, empty box or a different product."
    )
    model: str | None = Field(description="Model as written, e.g. 'Precision 5680'.")
    cpu: str | None = Field(description="CPU in canonical form, e.g. 'i7-13800H' or 'Core Ultra 7 165H'.")
    gpu: str | None = Field(description="Discrete GPU as claimed, e.g. 'RTX 2000 Ada'. 'integrated' if none.")
    gpu_source: FieldSource
    ram_gb: int | None
    ssd_gb: int | None = Field(description="Total SSD capacity in GB (1 TB = 1000).")
    screen: Screen
    touch: bool | None
    keyboard_layout: str | None = Field(description="e.g. 'FR AZERTY', 'DE QWERTZ', 'UK QWERTY'.")
    condition: Condition
    warranty_until: date | None = Field(description="Manufacturer or seller warranty end date if stated.")
    warranty_months: int | None = Field(description="Warranty length in months if stated as a duration.")
    battery_cycles: int | None
    battery_health_pct: int | None
    charger_included: bool | None
    service_tag: str | None
    locked: bool = Field(description="BIOS password, Computrace/Absolute or MDM lock mentioned.")
    payment_before_inspection: bool = Field(
        description="Seller requires payment (bank transfer, sealed box) before the buyer can test it."
    )
    template_text_suspected: bool = Field(
        description="Description contains specs that cannot belong to this listing (another model, other RAM type)."
    )
    conflicts: list[str] = Field(
        description="Contradictions between title, structured fields and description, one sentence each."
    )


# --- Enriched listing that flows through the deterministic stages. ---


class Validation(BaseModel):
    model_key: str | None = None
    cpu_key: str | None = None
    cpu_class: str | None = None
    gpu_key: str | None = None
    invalid: list[str] = Field(default_factory=list)  # values that do not exist for this model
    invalid_fields: list[str] = Field(default_factory=list)  # cpu | gpu | ram, matching `invalid`
    warnings: list[str] = Field(default_factory=list)


class Verdict(StrEnum):
    PASS = "pass"
    HOLD = "hold"  # needs confirmation from the seller
    REJECT = "reject"


class LandedCost(BaseModel):
    total_eur: Decimal
    parts: list[tuple[str, Decimal, bool]]  # (label, EUR amount, estimated?)
    notes: list[str] = Field(default_factory=list)

    @property
    def estimated(self) -> bool:
        return any(est for _, _, est in self.parts)


class FairValue(BaseModel):
    value_eur: Decimal
    parts: list[tuple[str, Decimal]]
    assumptions: list[str] = Field(default_factory=list)


class Scored(BaseModel):
    raw: RawListing
    spec: ExtractedSpec | None = None
    extraction_error: str | None = None
    validation: Validation = Field(default_factory=Validation)
    verdict: Verdict = Verdict.PASS
    verdict_reasons: list[str] = Field(default_factory=list)
    pickup_zone: str | None = None
    landed: LandedCost | None = None
    fair: FairValue | None = None
    discount: float | None = None
    risk: int = 0
    risk_reasons: list[str] = Field(default_factory=list)
    adjusted_discount: float | None = None
    duplicate_of: str | None = None  # key of the primary listing in a cross-source group
    duplicates: list[str] = Field(default_factory=list)
