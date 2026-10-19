"""Render a Quote as a branded, customer-facing PDF (ReportLab)."""
from __future__ import annotations

from datetime import date
from io import BytesIO
from xml.sax.saxutils import escape as esc

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from .models import Quote

BRAND = colors.HexColor("#1f4d3a")
ACCENT = colors.HexColor("#c8873a")
MUTED = colors.HexColor("#6b7280")
LINE = colors.HexColor("#e5e7eb")
ZEBRA = colors.HexColor("#f7f7f4")

ss = getSampleStyleSheet()
S = {
    "body": ParagraphStyle("body", parent=ss["Normal"], fontSize=9, leading=12),
    "small": ParagraphStyle("small", parent=ss["Normal"], fontSize=7.5, leading=10, textColor=MUTED),
    "h_item": ParagraphStyle("h_item", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=10,
                             textColor=BRAND, spaceBefore=6, spaceAfter=3),
    "label": ParagraphStyle("label", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=7.5,
                            textColor=MUTED, leading=10),
    "right": ParagraphStyle("right", parent=ss["Normal"], fontSize=9, alignment=TA_RIGHT),
}


def sup(text: str) -> str:
    """Helvetica has no '²' glyph in ReportLab's base fonts: use markup instead."""
    return text.replace("²", "<super>2</super>").replace("³", "<super>3</super>")


def gbp(x: float) -> str:
    return f"£{x:,.2f}"


def _fmt_qty(q: float) -> str:
    return f"{q:g}"


def _fmt_date(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d %b %Y")


def render_quote_pdf(q: Quote, business: dict) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=38 * mm, bottomMargin=16 * mm,
                            title=f"Quotation {q.reference}", author=business["name"])
    W = A4[0] - 32 * mm

    def header_footer(canvas, _doc):
        canvas.saveState()
        h = 30 * mm
        canvas.setFillColor(BRAND)
        canvas.rect(0, A4[1] - h, A4[0], h, stroke=0, fill=1)
        canvas.setFillColor(ACCENT)
        canvas.rect(0, A4[1] - h - 1.2 * mm, A4[0], 1.2 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 17)
        canvas.drawString(16 * mm, A4[1] - 14 * mm, business["name"])
        canvas.setFont("Helvetica", 8.5)
        canvas.drawString(16 * mm, A4[1] - 20 * mm, business["tagline"])
        canvas.drawString(16 * mm, A4[1] - 24.5 * mm,
                          f"{business['address']}  ·  {business['phone']}  ·  {business['email']}")
        canvas.setFont("Helvetica-Bold", 20)
        canvas.drawRightString(A4[0] - 16 * mm, A4[1] - 15 * mm, "QUOTATION")
        canvas.setFont("Helvetica", 8.5)
        canvas.drawRightString(A4[0] - 16 * mm, A4[1] - 21 * mm, q.reference)
        # footer
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(16 * mm, 10 * mm, business.get("demo_notice", ""))
        canvas.drawRightString(A4[0] - 16 * mm, 10 * mm, f"Page {_doc.page}")
        canvas.restoreState()

    story = []

    # --- customer / quote meta
    c = q.customer
    cust = "<br/>".join(x for x in [f"<b>{esc(c.name or 'Customer')}</b>", esc(c.address).replace("\n", "<br/>"), esc(c.email)] if x)
    meta = Table([
        [Paragraph("QUOTE FOR", S["label"]), Paragraph("DATE", S["label"]), Paragraph("VALID UNTIL", S["label"])],
        [Paragraph(cust, S["body"]), Paragraph(_fmt_date(q.date), S["body"]),
         Paragraph(_fmt_date(q.valid_until), S["body"])],
    ], colWidths=[W * 0.5, W * 0.25, W * 0.25])
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [meta, Spacer(1, 5 * mm)]

    if q.description:
        story += [Paragraph("YOUR REQUEST", S["label"]),
                  Paragraph(f"<i>“{esc(q.description.strip())}”</i>", S["body"]), Spacer(1, 4 * mm)]

    # --- one table per job item
    col_w = [W * 0.50, W * 0.10, W * 0.10, W * 0.14, W * 0.16]
    for n, item in enumerate(q.items, 1):
        rows = [["Description", "Qty", "Unit", "Unit price", "Amount"]]
        for cat in ("materials", "plant", "labour"):
            for l in (l for l in item.lines if l.category == cat):
                rows.append([Paragraph(sup(l.description), S["body"]), _fmt_qty(l.quantity),
                             Paragraph(sup(l.unit), S["body"]),
                             gbp(l.unit_price), gbp(l.total)])
        rows.append(["", "", "", "Subtotal", gbp(item.subtotal)])
        t = Table(rows, colWidths=col_w, repeatRows=1)
        style = [
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("ALIGN", (2, 0), (2, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, BRAND),
            ("LINEABOVE", (0, -1), (-1, -1), 0.5, LINE),
            ("FONT", (3, -1), (-1, -1), "Helvetica-Bold", 9),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
        for r in range(1, len(rows) - 1):
            if r % 2 == 0:
                style.append(("BACKGROUND", (0, r), (-1, r), ZEBRA))
        t.setStyle(TableStyle(style))
        notes = [Paragraph("• " + sup(a), S["small"]) for a in item.assumptions]
        if item.item.notes:
            notes.append(Paragraph("• " + sup(esc(item.item.notes)), S["small"]))
        story.append(KeepTogether([Paragraph(sup(f"{n}. {esc(item.title)}"), S["h_item"]), t, Spacer(1, 1.5 * mm), *notes]))
        story.append(Spacer(1, 2.5 * mm))

    # --- totals
    s = q.settings
    items_total = q.materials_total + q.labour_total + q.plant_total
    tot_rows = [
        ["Works total", gbp(items_total)],
        [f"Contingency ({s['contingency_pct']:g}%)", gbp(q.contingency)],
        ["Total (ex. VAT)", gbp(q.net_total)],
        [f"VAT ({s['vat_pct']:g}%)", gbp(q.vat)],
        ["TOTAL", gbp(q.grand_total)],
    ]
    tot = Table(tot_rows, colWidths=[W * 0.24, W * 0.16], hAlign="RIGHT")
    tot.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, 2), (-1, 2), 0.5, LINE),
        ("BACKGROUND", (0, -1), (-1, -1), BRAND),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 11),
        ("TOPPADDING", (0, -1), (-1, -1), 6), ("BOTTOMPADDING", (0, -1), (-1, -1), 6),
    ]))

    terms = (
        f"<b>Terms.</b> This quotation is valid until {_fmt_date(q.valid_until)}. Prices are based on the "
        "measurements and assumptions listed above and are subject to a site survey. A 25% deposit secures "
        "your start date; the balance is due on completion. Any variations will be agreed in writing before work starts."
    )
    story.append(KeepTogether([tot, Spacer(1, 6 * mm), Paragraph(terms, S["small"])]))

    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    return buf.getvalue()
