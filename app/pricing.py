"""Deterministic pricing engine.

The AI never produces a price. It only turns free text into structured
`JobItem`s; every quantity and every pound below is computed here from the
editable price book (price_book.json), so each figure can be traced back to a
rule and a unit price.
"""
from __future__ import annotations

import json
import math
import secrets
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from .models import JOB_TYPES, Customer, JobItem, PricedItem, Quote, QuoteLine

PRICE_BOOK_PATH = Path(__file__).with_name("price_book.json")


def load_price_book(path: Path = PRICE_BOOK_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class PricingError(ValueError):
    """Raised when an item is missing the measurements needed to price it."""


# ---------------------------------------------------------------- helpers
def ceil_to(x: float, step: float) -> float:
    """Round up to the next multiple of `step` (e.g. 0.5 tonne, 0.25 m3)."""
    return round(math.ceil(round(x / step, 9)) * step, 4)


def money(x: float) -> float:
    return round(x + 1e-9, 2)


class _Builder:
    """Collects quote lines for one item, applying markup to materials."""

    def __init__(self, pb: dict):
        self.pb = pb
        self.markup = 1 + pb["settings"]["materials_markup_pct"] / 100
        self.min_days = pb["settings"]["min_labour_days"]
        self.lines: list[QuoteLine] = []
        self.assumptions: list[str] = []

    def material(self, key: str, qty: float, note: str = "") -> None:
        if qty <= 0:
            return
        m = self.pb["materials"][key]
        unit_price = money(m["price"] * self.markup)
        desc = m["label"] + (f" ({note})" if note else "")
        self.lines.append(QuoteLine(category="materials", description=desc, quantity=qty,
                                    unit=m["unit"], unit_price=unit_price,
                                    total=money(qty * unit_price)))

    def plant(self, key: str, qty: float) -> None:
        if qty <= 0:
            return
        p = self.pb["plant"][key]
        self.lines.append(QuoteLine(category="plant", description=p["label"], quantity=qty,
                                    unit=p["unit"], unit_price=p["price"],
                                    total=money(qty * p["price"])))

    def labour(self, trade: str, days: float) -> None:
        days = max(self.min_days, ceil_to(days, 0.5))
        rate = self.pb["labour_day_rates"][trade]
        self.lines.append(QuoteLine(category="labour", description=trade.replace("_", " ").title(),
                                    quantity=days, unit="day", unit_price=rate,
                                    total=money(days * rate)))

    def assume(self, text: str) -> None:
        self.assumptions.append(text)


def _area(item: JobItem, second: str = "width_m") -> float:
    """Area from area_m2, or length x width (or length x height for walls)."""
    if item.area_m2:
        return item.area_m2
    other = getattr(item, second)
    if item.length_m and other:
        return round(item.length_m * other, 3)
    dims = "length and width" if second == "width_m" else "length and height"
    raise PricingError(f"{JOB_TYPES[item.type]['label']}: please give an area or {dims}.")


def _need(value, name: str, item: JobItem):
    if value is None:
        raise PricingError(f"{JOB_TYPES[item.type]['label']}: missing {name}.")
    return value


def _skips(volume_m3: float) -> int:
    """8-yard skip holds ~6 m3 of bulked spoil."""
    return math.ceil(volume_m3 / 6) if volume_m3 > 0.3 else 0


# ---------------------------------------------------------------- job rules
def price_patio(item: JobItem, b: _Builder) -> str:
    area = _area(item)
    material = item.material or "sandstone"
    b.material(f"slab_{material}_m2", math.ceil(area * 1.10), "incl. 10% cuts")
    b.material("membrane_m2", math.ceil(area * 1.10))
    b.material("mot_type1_t", ceil_to(area * 0.10 * 2.1, 0.5), "100mm compacted")
    mortar = area * 0.04
    b.material("sharp_sand_t", ceil_to(mortar * 1.6, 0.5), "40mm bed")
    b.material("cement_bag", math.ceil(mortar * 6))
    b.material("jointing_tub", math.ceil(area / 15))
    b.plant("skip_8yd", _skips(area * 0.15 * 1.3))
    if area > 20:
        b.plant("mini_digger_day", math.ceil(area / 40))
    b.labour("landscaper", area / 8)
    b.labour("labourer", area / 8)
    b.assume(f"{area:g} m² excavated to 150mm, laid on 100mm MOT Type 1 and a full mortar bed.")
    b.assume("Good access for a skip/wheelbarrow; no drainage alterations.")
    return f"{material.title()} patio – {area:g} m²"


def price_brick_wall(item: JobItem, b: _Builder) -> str:
    length = _need(item.length_m, "length", item)
    height = item.height_m or JOB_TYPES["brick_wall"]["defaults"]["height_m"]
    skins = 2 if (item.thickness or "double") == "double" else 1
    wall_area = length * height
    bricks = math.ceil(wall_area * 60 * skins * 1.05 / 10) * 10
    b.material("brick_facing", bricks, "60/m² per skin + 5%")
    mortar = wall_area * 0.023 * skins
    b.material("building_sand_t", ceil_to(mortar * 1.6, 0.5))
    b.material("cement_bag", math.ceil(mortar * 7))
    footing_w = 0.6 if skins == 2 else 0.45
    footing = length * footing_w * 0.3
    b.material("readymix_m3", ceil_to(footing * 1.05, 0.25), f"footing {footing_w}m x 300mm")
    b.material("coping_m", math.ceil(length))
    brick_days = bricks / 450
    b.plant("mixer_day", math.ceil(brick_days))
    b.plant("skip_8yd", _skips(footing * 1.3))
    b.labour("bricklayer", brick_days)
    b.labour("labourer", brick_days + 0.5)
    b.assume(f"{length:g}m x {height:g}m {'double' if skins == 2 else 'single'}-skin wall on a concrete strip footing.")
    if height > 1.2 and skins == 1:
        b.assume("Single-skin walls over 1.2m need piers – recommend a site check.")
    return f"Brick wall – {length:g}m x {height:g}m"


def price_fence(item: JobItem, b: _Builder) -> str:
    length = _need(item.length_m, "length", item)
    height = item.height_m or 1.8
    panels = math.ceil(length / 1.83)
    posts = panels + 1
    b.material("fence_panel", panels, f"{height:g}m high")
    b.material("fence_post", posts)
    b.material("gravel_board", panels)
    b.material("postcrete_bag", posts * 2)
    b.labour("landscaper", panels / 7)
    b.labour("labourer", panels / 7)
    b.assume(f"{panels} panels / {posts} posts for a {length:g}m run; removal of an old fence not included.")
    if height > 1.8:
        b.assume("Fences over 2m (incl. trellis) may need planning permission.")
    return f"Fencing – {length:g}m run, {height:g}m high"


def price_concrete_base(item: JobItem, b: _Builder) -> str:
    area = _area(item)
    depth = item.depth_mm or 100
    b.material("readymix_m3", ceil_to(area * depth / 1000 * 1.05, 0.25), f"{depth:g}mm slab")
    b.material("mot_type1_t", ceil_to(area * 0.10 * 2.1, 0.5), "100mm sub-base")
    b.material("dpm_m2", math.ceil(area * 1.10))
    b.material("mesh_sheet", math.ceil(area / 10.5))
    b.plant("skip_8yd", _skips(area * (depth + 100) / 1000 * 1.3))
    b.labour("groundworker", 0.5 + area / 15)
    b.labour("labourer", 0.5 + area / 15)
    b.assume(f"{area:g} m² slab, {depth:g}mm C25 concrete with A142 mesh on DPM and MOT Type 1.")
    return f"Concrete base – {area:g} m² x {depth:g}mm"


def price_plastering(item: JobItem, b: _Builder) -> str:
    area = _area(item, "height_m")
    b.material("multifinish_bag", math.ceil(area * 1.1 / 10), "~10 m² per bag")
    b.material("pva_tub", math.ceil(area / 30))
    b.material("plaster_sundries_m2", math.ceil(area))
    b.labour("plasterer", area / 30)
    b.assume(f"Skim coat over sound plasterboard/backing, {area:g} m².")
    return f"Plastering (skim) – {area:g} m²"


def price_painting(item: JobItem, b: _Builder) -> str:
    area = _area(item, "height_m")
    coats = item.coats or 2
    litres = area * coats / 12 * 1.1
    b.material("emulsion_5l", math.ceil(litres / 5), f"{coats} coats @ 12 m²/L")
    b.material("paint_sundries_m2", math.ceil(area))
    b.labour("decorator", area * coats / 55)
    b.assume(f"{area:g} m² of walls/ceilings, {coats} coats of emulsion; furniture moved by customer.")
    return f"Painting – {area:g} m², {coats} coats"


def price_flooring(item: JobItem, b: _Builder) -> str:
    area = _area(item)
    b.material("laminate_pack", math.ceil(area * 1.10 / 2.2), "incl. 10% waste")
    b.material("underlay_m2", math.ceil(area * 1.05))
    b.material("floor_trims_m2", math.ceil(area))
    b.labour("floor_fitter", area / 18)
    b.assume(f"{area:g} m² over a level, dry subfloor; old flooring removal not included.")
    return f"Laminate flooring – {area:g} m²"


PRICERS: dict[str, Callable[[JobItem, _Builder], str]] = {
    "patio": price_patio,
    "brick_wall": price_brick_wall,
    "fence": price_fence,
    "concrete_base": price_concrete_base,
    "plastering": price_plastering,
    "painting": price_painting,
    "flooring": price_flooring,
}


def price_item(item: JobItem, pb: dict) -> PricedItem:
    b = _Builder(pb)
    title = PRICERS[item.type](item, b)
    if item.label:
        title = f"{item.label} – {title.split(' – ', 1)[-1]}"
    return PricedItem(item=item, title=title, lines=b.lines, assumptions=b.assumptions,
                      subtotal=money(sum(l.total for l in b.lines)))


def build_quote(items: list[JobItem], customer: Customer | None = None,
                description: str = "", pb: dict | None = None,
                today: date | None = None, reference: str | None = None) -> Quote:
    pb = pb or load_price_book()
    s = pb["settings"]
    today = today or date.today()
    priced = [price_item(i, pb) for i in items]
    all_lines = [l for p in priced for l in p.lines]

    def total(cat: str) -> float:
        return money(sum(l.total for l in all_lines if l.category == cat))

    materials, labour, plant = total("materials"), total("labour"), total("plant")
    markup_factor = 1 + s["materials_markup_pct"] / 100
    materials_markup = money(materials - materials / markup_factor)
    contingency = money((materials + labour + plant) * s["contingency_pct"] / 100)
    net = money(materials + labour + plant + contingency)
    vat = money(net * s["vat_pct"] / 100)

    return Quote(
        reference=reference or f"OAK-{today:%y%m%d}-{secrets.token_hex(2).upper()}",
        date=today.isoformat(),
        valid_until=(today + timedelta(days=s["quote_valid_days"])).isoformat(),
        customer=customer or Customer(),
        description=description,
        items=priced,
        materials_total=materials,
        materials_markup=materials_markup,
        labour_total=labour,
        plant_total=plant,
        contingency=contingency,
        net_total=net,
        vat=vat,
        grand_total=money(net + vat),
        settings=s,
    )
