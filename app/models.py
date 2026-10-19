"""Data models shared by the extractor, the pricing engine and the API."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

JobType = Literal[
    "patio",
    "brick_wall",
    "fence",
    "concrete_base",
    "plastering",
    "painting",
    "flooring",
]

# Metadata used by the UI (which inputs to show) and by the AI prompt.
JOB_TYPES: dict[str, dict] = {
    "patio": {
        "label": "Patio / paving",
        "fields": ["length_m", "width_m", "area_m2", "material"],
        "options": {"material": ["sandstone", "porcelain", "concrete"]},
        "defaults": {"material": "sandstone"},
        "hint": "Area from length x width, or area directly.",
    },
    "brick_wall": {
        "label": "Brick garden wall",
        "fields": ["length_m", "height_m", "thickness"],
        "options": {"thickness": ["single", "double"]},
        "defaults": {"height_m": 1.0, "thickness": "double"},
        "hint": "Single skin = half-brick; double skin = full brick (free-standing walls).",
    },
    "fence": {
        "label": "Fencing",
        "fields": ["length_m", "height_m"],
        "options": {},
        "defaults": {"height_m": 1.8},
        "hint": "Panels with concrete posts and gravel boards.",
    },
    "concrete_base": {
        "label": "Concrete base / slab",
        "fields": ["length_m", "width_m", "area_m2", "depth_mm"],
        "options": {},
        "defaults": {"depth_mm": 100},
        "hint": "e.g. shed or garage base. Includes sub-base, DPM and mesh.",
    },
    "plastering": {
        "label": "Plastering (skim)",
        "fields": ["area_m2", "length_m", "height_m"],
        "options": {},
        "defaults": {},
        "hint": "Wall or ceiling area to skim.",
    },
    "painting": {
        "label": "Interior painting",
        "fields": ["area_m2", "length_m", "height_m", "coats"],
        "options": {},
        "defaults": {"coats": 2},
        "hint": "Wall/ceiling area to paint.",
    },
    "flooring": {
        "label": "Laminate flooring",
        "fields": ["length_m", "width_m", "area_m2"],
        "options": {},
        "defaults": {},
        "hint": "Floor area; 10% cutting waste is added.",
    },
}


class JobItem(BaseModel):
    """One line of work extracted from the customer's description."""

    type: JobType
    label: Optional[str] = Field(None, description="Short human label, e.g. 'Back garden patio'")
    length_m: Optional[float] = Field(None, gt=0, le=500)
    width_m: Optional[float] = Field(None, gt=0, le=500)
    height_m: Optional[float] = Field(None, gt=0, le=10)
    area_m2: Optional[float] = Field(None, gt=0, le=5000)
    depth_mm: Optional[float] = Field(None, gt=0, le=500)
    material: Optional[Literal["sandstone", "porcelain", "concrete"]] = None
    thickness: Optional[Literal["single", "double"]] = None
    coats: Optional[int] = Field(None, ge=1, le=4)
    notes: Optional[str] = None


class ExtractRequest(BaseModel):
    description: str = Field(..., min_length=3, max_length=4000)


class ExtractResponse(BaseModel):
    items: list[JobItem]
    questions: list[str] = []
    source: Literal["ai", "rules"]
    warning: Optional[str] = None


class Customer(BaseModel):
    name: str = ""
    address: str = ""
    email: str = ""


class QuoteRequest(BaseModel):
    items: list[JobItem] = Field(..., min_length=1, max_length=20)
    customer: Customer = Customer()
    description: str = Field("", max_length=4000)
    reference: Optional[str] = Field(None, pattern=r"^[A-Z]{2,5}-\d{6}-[0-9A-F]{4}$")


class QuoteLine(BaseModel):
    category: Literal["materials", "labour", "plant"]
    description: str
    quantity: float
    unit: str
    unit_price: float
    total: float


class PricedItem(BaseModel):
    item: JobItem
    title: str
    lines: list[QuoteLine]
    assumptions: list[str]
    subtotal: float


class Quote(BaseModel):
    reference: str
    date: str
    valid_until: str
    customer: Customer
    description: str
    items: list[PricedItem]
    materials_total: float
    materials_markup: float
    labour_total: float
    plant_total: float
    contingency: float
    net_total: float
    vat: float
    grand_total: float
    settings: dict
