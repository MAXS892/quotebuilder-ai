from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_and_index():
    assert client.get("/api/health").json()["status"] == "ok"
    r = client.get("/")
    assert r.status_code == 200 and "QuoteBuilder AI" in r.text


def test_extract_then_quote_then_pdf(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    ex = client.post("/api/extract", json={"description": "A 5x4m patio and a 12m fence"}).json()
    assert {i["type"] for i in ex["items"]} == {"patio", "fence"}

    body = {"items": ex["items"], "customer": {"name": "Jane Smith"}, "description": "A 5x4m patio and a 12m fence"}
    q = client.post("/api/quote", json=body).json()
    assert q["grand_total"] > 0 and len(q["items"]) == 2

    body["reference"] = q["reference"]
    pdf = client.post("/api/quote/pdf", json=body)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF"
    assert q["reference"] in pdf.headers["content-disposition"]


def test_quote_with_missing_dimensions_returns_422():
    r = client.post("/api/quote", json={"items": [{"type": "patio"}]})
    assert r.status_code == 422
    assert "area" in r.json()["detail"]


def test_invalid_values_rejected():
    r = client.post("/api/quote", json={"items": [{"type": "fence", "length_m": -3}]})
    assert r.status_code == 422


def test_pdf_handles_special_characters():
    body = {"items": [{"type": "fence", "length_m": 10, "label": "Fence <rear> & side", "notes": "a < b"}],
            "customer": {"name": "O'Neil & Sons <Ltd>", "address": "1 Road\nTown"},
            "description": "Fence & gate <urgent>"}
    r = client.post("/api/quote/pdf", json=body)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
