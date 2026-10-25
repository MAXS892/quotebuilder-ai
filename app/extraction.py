"""Turn a customer's free-text job description into structured JobItems.

Two strategies:
  * `extract_with_ai`  – Google Gemini with a strict JSON schema (used when GEMINI_API_KEY is set)
  * `extract_with_rules` – a regex/keyword parser, used as a fallback so the demo
    always works (no key, API outage, rate limit...).
"""
from __future__ import annotations

import json
import logging
import os
import re

import httpx
from pydantic import ValidationError

from .models import JOB_TYPES, ExtractResponse, JobItem

log = logging.getLogger(__name__)

DEFAULT_ROOM_HEIGHT = 2.4

# ======================================================================
#  Rule-based fallback
# ======================================================================
NUM = r"(\d+(?:\.\d+)?)"
WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "single": 1, "double": 2}

# Order matters: the first matching type wins for a clause.
TYPE_PATTERNS: list[tuple[str, str]] = [
    ("painting", r"\b(re-?)?(paint\w*|decorat\w*)\b|\bemulsion\b"),
    ("plastering", r"\b(plaster\w*|re-?skim\w*|skim\w*)\b"),
    ("flooring", r"\b(laminate|flooring|floorboards?|new floor)\b"),
    ("fence", r"\b(fenc\w*|fence panels?)\b"),
    ("concrete_base", r"\b(concrete (base|slab|pad)|(shed|garage|summerhouse) base|base for (a |the |my )?\w+|hardstanding)\b"),
    ("patio", r"\b(patio|paving|paved|pavers|terrace)\b"),
    ("brick_wall", r"\b(brick ?wall|garden wall|wall|brickwork)\b"),
]


def _normalise(text: str) -> str:
    t = text.lower().replace("×", "x").replace("²", "2").replace("’", "'")
    # feet -> metres
    t = re.sub(rf"{NUM}\s*(?:ft|feet|foot)\b",
               lambda m: f"{float(m.group(1)) * 0.3048:.2f}m", t)
    t = re.sub(r"\b(square|sq\.?)\s*(metres?|meters?|m)\b", "m2", t)
    t = re.sub(r"\bsqm\b", "m2", t)
    t = re.sub(r"\b(metres?|meters?|mtrs?)\b", "m", t)
    t = re.sub(r"(\d)\s+m\b", r"\1m", t)
    return t


def _types_in(clause: str) -> set[str]:
    return {jt for jt, pat in TYPE_PATTERNS if re.search(pat, clause)}


def _split_clauses(t: str) -> list[str]:
    # Sentence / list separators (a "." between digits is a decimal point, not a separator).
    parts = re.split(r"(?<!\d)\.|\.(?!\d)|[;\n,]|\balso\b|\bplus\b|\bas well as\b", t)
    clauses: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # "a patio and a fence" -> split on "and" only if the clause mentions 2+ job types,
        # so "8m long and 1m high" stays together.
        if len(_types_in(p)) > 1:
            clauses += [q.strip() for q in re.split(r"\band\b|\bthen\b|\bwith\b", p) if q.strip()]
        else:
            clauses.append(p)
    return clauses


def _parse_dims(clause: str, job_type: str | None) -> dict:
    d: dict = {}
    is_room = bool(re.search(r"\b(room|bedroom|kitchen|lounge|bathroom|hallway|hall|office|dining)\b", clause))
    vertical = job_type in ("brick_wall", "fence") or (
        job_type in ("painting", "plastering") and not is_room)

    m = re.search(rf"{NUM}\s*m2\b", clause)
    if m:
        d["area_m2"] = float(m.group(1))

    m = re.search(rf"{NUM}\s*m?\s*(?:x|by)\s*{NUM}\s*m?\b(?!m)", clause)
    if m and "area_m2" not in d:
        a, b = float(m.group(1)), float(m.group(2))
        if vertical:
            d["length_m"], d["height_m"] = max(a, b), min(a, b)
        else:
            d["length_m"], d["width_m"] = a, b

    m = re.search(rf"{NUM}m\s*(?:high|tall|in height)\b|\bheight of\s*{NUM}m", clause)
    if m:
        d["height_m"] = float(m.group(1) or m.group(2))

    m = re.search(rf"{NUM}m\s*(?:long|in length|run|of)\b|\blength of\s*{NUM}m|\b{NUM}m\s+(?=\w*\s*(?:fenc|wall))", clause)
    if m and "length_m" not in d:
        d["length_m"] = float(next(g for g in m.groups() if g))

    m = re.search(rf"{NUM}\s*mm\b", clause)
    if m:
        d["depth_mm"] = float(m.group(1))

    m = re.search(r"\b(\d|one|two|three|four)\s+coats?\b", clause)
    if m:
        g = m.group(1)
        d["coats"] = int(g) if g.isdigit() else WORD_NUMS[g]

    for mat in ("sandstone", "porcelain"):
        if mat in clause:
            d["material"] = mat
    if re.search(r"concrete (slabs?|paving)", clause):
        d["material"] = "concrete"

    if re.search(r"single[- ]skin|half[- ]brick|single brick", clause):
        d["thickness"] = "single"
    elif re.search(r"double[- ]skin|full[- ]brick|double brick", clause):
        d["thickness"] = "double"

    # A lone "12m" with no qualifier: treat as a length.
    if not any(k in d for k in ("length_m", "area_m2")):
        nums = [float(x) for x in re.findall(rf"(?<![\d.]){NUM}m\b(?!2|m)", clause)]
        nums = [n for n in nums if n != d.get("height_m")]
        if nums:
            d["length_m"] = nums[0]
    return d


def _room_to_wall_area(item: dict) -> dict:
    """Painting/plastering described as a room 'L x W' -> wall area."""
    if item.get("width_m") and not item.get("area_m2"):
        h = item.pop("height_m", None) or DEFAULT_ROOM_HEIGHT
        length, width = item.pop("length_m"), item.pop("width_m")
        item["area_m2"] = round(2 * (length + width) * h, 1)
        item["notes"] = f"Walls of a {length:g} x {width:g}m room with {h:g}m ceilings"
    return item


def _missing_question(item: JobItem) -> str | None:
    label = JOB_TYPES[item.type]["label"].lower()
    if item.type in ("brick_wall", "fence") and not item.length_m:
        return f"How long should the {label} be (in metres)?"
    if item.type in ("patio", "concrete_base", "flooring") and not (
            item.area_m2 or (item.length_m and item.width_m)):
        return f"What size is the {label} area (length x width)?"
    if item.type in ("plastering", "painting") and not (
            item.area_m2 or (item.length_m and item.height_m)):
        return f"Roughly how many m² of wall/ceiling for the {label}?"
    return None


def extract_with_rules(description: str) -> ExtractResponse:
    t = _normalise(description)
    raw: list[dict] = []
    for clause in _split_clauses(t):
        job_type = next((jt for jt, pat in TYPE_PATTERNS if re.search(pat, clause)), None)
        if job_type:
            raw.append({"type": job_type, **_parse_dims(clause, job_type)})
        elif raw:  # descriptive clause ("in sandstone", "about 1.8m high") -> previous item
            for k, v in _parse_dims(clause, raw[-1]["type"]).items():
                raw[-1].setdefault(k, v)

    items: list[JobItem] = []
    for r in raw:
        if r["type"] in ("painting", "plastering"):
            r = _room_to_wall_area(r)
        try:
            items.append(JobItem(**r))
        except ValidationError:
            items.append(JobItem(type=r["type"]))
    questions = [q for q in (_missing_question(i) for i in items) if q]
    if not items:
        questions.append("I couldn't identify the work. Could you mention the job type "
                         "(e.g. patio, fence, brick wall, concrete base, plastering, painting, flooring) and sizes?")
    return ExtractResponse(items=items, questions=questions, source="rules")


# ======================================================================
#  AI extraction (Google Gemini, free tier)
# ======================================================================
SYSTEM_PROMPT = """You are an estimator's assistant for a small UK building firm.
Convert the customer's description into a list of job items. Rules:
- Only use these job types: {types}.
- Use metres, m² and mm. Convert feet/inches if needed.
- patio/concrete_base/flooring: give length_m and width_m, or area_m2.
- brick_wall/fence: length_m is the run length, height_m the height.
- painting/plastering: area_m2 is the WALL/CEILING area. If the customer gives a room's
  floor size (L x W), compute walls = 2*(L+W)*height (assume 2.4m ceilings) and explain in notes.
- Never invent measurements. If something essential is missing, leave it null and add a short
  question to `questions`. Ignore work that is not one of the job types, but mention it in questions.
- `label` is a short friendly name (e.g. "Back garden patio"). Never output prices.
"""

_NULLABLE_NUM = {"type": "NUMBER", "nullable": True}
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "type": {"type": "STRING", "enum": list(JOB_TYPES)},
                    "label": {"type": "STRING", "nullable": True},
                    "length_m": _NULLABLE_NUM,
                    "width_m": _NULLABLE_NUM,
                    "height_m": _NULLABLE_NUM,
                    "area_m2": _NULLABLE_NUM,
                    "depth_mm": _NULLABLE_NUM,
                    "material": {"type": "STRING", "enum": ["sandstone", "porcelain", "concrete"], "nullable": True},
                    "thickness": {"type": "STRING", "enum": ["single", "double"], "nullable": True},
                    "coats": {"type": "INTEGER", "nullable": True},
                    "notes": {"type": "STRING", "nullable": True},
                },
                "required": ["type"],
            },
        },
        "questions": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["items", "questions"],
}


def ai_enabled() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def extract_with_ai(description: str, timeout: float = 25.0) -> ExtractResponse:
    key = os.environ["GEMINI_API_KEY"]
    model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT.format(types=", ".join(JOB_TYPES))}]},
        "contents": [{"role": "user", "parts": [{"text": description}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    r = httpx.post(url, json=body, headers={"x-goog-api-key": key}, timeout=timeout)
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    data = json.loads(text)

    items, dropped = [], 0
    for raw in data.get("items", []):
        clean = {k: v for k, v in raw.items() if v is not None}
        try:
            items.append(JobItem(**clean))
        except ValidationError:
            dropped += 1
    questions = list(data.get("questions", []))
    questions += [q for q in (_missing_question(i) for i in items) if q and q not in questions]
    warning = f"{dropped} item(s) had invalid values and were skipped." if dropped else None
    return ExtractResponse(items=items, questions=questions, source="ai", warning=warning)


def extract(description: str) -> ExtractResponse:
    """Use AI when configured; fall back to rules on any failure."""
    if ai_enabled():
        try:
            return extract_with_ai(description)
        except Exception as exc:  # network, quota, bad JSON...
            log.warning("AI extraction failed, using rules: %s", exc)
            res = extract_with_rules(description)
            res.warning = "AI service unavailable – used the built-in rule parser instead."
            return res
    return extract_with_rules(description)
