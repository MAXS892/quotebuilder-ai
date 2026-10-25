import json

import httpx
import pytest

from app import extraction
from app.extraction import extract, extract_with_rules


def by_type(res):
    return {i.type: i for i in res.items}


def test_patio_and_fence_in_one_sentence():
    r = extract_with_rules("I'd like a 5x4m sandstone patio and about 12 metres of fencing, 1.8m high.")
    items = by_type(r)
    assert items["patio"].length_m == 5 and items["patio"].width_m == 4
    assert items["patio"].material == "sandstone"
    assert items["fence"].length_m == 12 and items["fence"].height_m == 1.8


def test_decimals_and_depth():
    r = extract_with_rules("A concrete base for my shed 3m by 2.5m, 150mm thick.")
    base = by_type(r)["concrete_base"]
    assert (base.length_m, base.width_m, base.depth_mm) == (3, 2.5, 150)


def test_room_size_becomes_wall_area():
    r = extract_with_rules("Repaint my living room which is 5m by 4m, two coats")
    paint = by_type(r)["painting"]
    assert paint.area_m2 == pytest.approx(2 * (5 + 4) * 2.4)
    assert paint.coats == 2


def test_feet_converted():
    r = extract_with_rules("We need a 30ft fence")
    assert by_type(r)["fence"].length_m == pytest.approx(9.14, abs=0.01)


def test_missing_size_asks_question():
    r = extract_with_rules("Need a new patio please")
    assert r.items[0].type == "patio"
    assert any("size" in q for q in r.questions)


def test_unknown_work_asks_for_clarification():
    r = extract_with_rules("Fix my roof")
    assert r.items == [] and r.questions


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = "error" if status_code >= 400 else ""

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(self._payload)}]}}]}


def test_ai_path_validates_output(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    payload = {"items": [{"type": "patio", "area_m2": 20, "material": "porcelain", "label": "Back patio"},
                         {"type": "fence", "length_m": -5}],          # invalid -> dropped
               "questions": []}
    monkeypatch.setattr(extraction.httpx, "post", lambda *a, **k: _FakeResponse(payload))
    r = extract("anything")
    assert r.source == "ai"
    assert len(r.items) == 1 and r.items[0].label == "Back patio"
    assert r.warning


def test_ai_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(extraction.httpx, "post", boom)
    r = extract("a 10m fence")
    assert r.source == "rules" and r.items[0].type == "fence"
    assert "unavailable" in r.warning


def test_ai_retries_without_thinking_config(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    calls = []

    def fake_post(url, json, **k):
        calls.append("thinkingConfig" in json["generationConfig"])
        if len(calls) == 1:
            return _FakeResponse({}, status_code=400)
        return _FakeResponse({"items": [{"type": "fence", "length_m": 12}], "questions": []})

    monkeypatch.setattr(extraction.httpx, "post", fake_post)
    r = extract("a fence")
    assert calls == [True, False]
    assert r.source == "ai" and r.items[0].length_m == 12


def test_ai_http_error_falls_back(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(extraction.httpx, "post", lambda *a, **k: _FakeResponse({}, status_code=429))
    r = extract("a 10m fence")
    assert r.source == "rules"
