from datetime import date

import pytest

from app.models import JobItem
from app.pricing import PricingError, build_quote, ceil_to, load_price_book, price_item

PB = load_price_book()


def qty(priced, label_start):
    return next(l.quantity for l in priced.lines if l.description.startswith(label_start))


def test_ceil_to():
    assert ceil_to(2.01, 0.5) == 2.5
    assert ceil_to(2.0, 0.5) == 2.0
    assert ceil_to(0.26, 0.25) == 0.5


def test_patio_quantities():
    p = price_item(JobItem(type="patio", length_m=5, width_m=4), PB)
    assert qty(p, "Indian sandstone") == 22          # 20 m2 + 10% cuts
    assert qty(p, "MOT Type 1") == 4.5               # 20 * 0.1 * 2.1 = 4.2 t -> 4.5
    assert qty(p, "Paving jointing") == 2            # 20 / 15 -> 2 tubs
    assert qty(p, "Landscaper") == 2.5               # 20 / 8 = 2.5 days


def test_patio_material_changes_price():
    sand = price_item(JobItem(type="patio", area_m2=20, material="sandstone"), PB)
    porc = price_item(JobItem(type="patio", area_m2=20, material="porcelain"), PB)
    assert porc.subtotal > sand.subtotal


def test_fence_panels_and_posts():
    p = price_item(JobItem(type="fence", length_m=12), PB)
    assert qty(p, "Fence panel") == 7                # 12 / 1.83 -> 7
    assert qty(p, "Concrete slotted post") == 8
    assert qty(p, "Postcrete") == 16


def test_double_skin_wall_uses_twice_the_bricks():
    single = price_item(JobItem(type="brick_wall", length_m=10, height_m=1, thickness="single"), PB)
    double = price_item(JobItem(type="brick_wall", length_m=10, height_m=1, thickness="double"), PB)
    assert qty(single, "Facing bricks") == 630
    assert qty(double, "Facing bricks") == 1260


def test_painting_area_from_length_and_height():
    p = price_item(JobItem(type="painting", length_m=14, height_m=2.5, coats=2), PB)
    assert "35 m²" in p.title
    assert qty(p, "Trade emulsion") == 2             # 35*2/12*1.1 = 6.4 L -> 2 tins


def test_minimum_labour_half_day():
    p = price_item(JobItem(type="plastering", area_m2=3), PB)
    assert qty(p, "Plasterer") == 0.5


def test_missing_dimensions_raise_friendly_error():
    with pytest.raises(PricingError, match="area or length and width"):
        price_item(JobItem(type="patio", length_m=5), PB)


def test_quote_totals_add_up():
    q = build_quote([JobItem(type="patio", area_m2=20), JobItem(type="fence", length_m=12)],
                    today=date(2026, 10, 19))
    lines = [l for i in q.items for l in i.lines]
    assert q.materials_total + q.labour_total + q.plant_total == pytest.approx(sum(l.total for l in lines), abs=0.01)
    assert q.contingency == pytest.approx((q.materials_total + q.labour_total + q.plant_total) * 0.05, abs=0.01)
    assert q.vat == pytest.approx(q.net_total * 0.20, abs=0.01)
    assert q.grand_total == pytest.approx(q.net_total + q.vat, abs=0.01)
    assert q.valid_until == "2026-11-18"
    assert 0 < q.materials_markup < q.materials_total
