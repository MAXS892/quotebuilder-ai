"""QuoteBuilder AI – FastAPI application."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .extraction import ai_enabled, extract
from .models import JOB_TYPES, ExtractRequest, ExtractResponse, Quote, QuoteRequest
from .pdf import render_quote_pdf
from .pricing import PricingError, build_quote, load_price_book

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

STATIC = Path(__file__).with_name("static")

app = FastAPI(
    title="QuoteBuilder AI",
    description="Turn a customer's job description into a priced building quote and PDF.",
    version="1.0.0",
)


@app.get("/api/health")
def health():
    return {"status": "ok", "ai_enabled": ai_enabled()}


@app.get("/api/config")
def config():
    pb = load_price_book()
    return {"job_types": JOB_TYPES, "business": pb["business"], "settings": pb["settings"],
            "ai_enabled": ai_enabled()}


@app.get("/api/price-book")
def price_book():
    return load_price_book()


@app.post("/api/extract", response_model=ExtractResponse)
def extract_items(req: ExtractRequest):
    return extract(req.description)


def _quote(req: QuoteRequest) -> Quote:
    try:
        return build_quote(req.items, req.customer, req.description, reference=req.reference)
    except PricingError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/quote", response_model=Quote)
def quote(req: QuoteRequest):
    return _quote(req)


@app.post("/api/quote/pdf")
def quote_pdf(req: QuoteRequest):
    q = _quote(req)
    pdf = render_quote_pdf(q, load_price_book()["business"])
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{q.reference}.pdf"'})


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
