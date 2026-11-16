"""Stage 5 - Normalise + validate, then decide: accepted / review / missing.

Three layers of checks:
  1. Evidence check   - does the quoted snippet (and the number) really appear
                        on the cited page? Catches hallucinations and page errors.
  2. Field rules      - type/range checks, unit conversion, year-on-year jumps.
  3. Cross-field rules- accounting identities that must hold between fields
                        (Scope 1+2+3 = total, water balance, board arithmetic).
Anything that fails, or has low confidence, goes to the human review queue.
"""
from __future__ import annotations

import re
from typing import Optional

from .normalize import convert, parse_bool, parse_number, parse_percent
from .schema import Document, Extraction, Field

CONFIDENCE_THRESHOLD = 0.7
YOY_THRESHOLD = 0.5          # >50 % change vs prior year gets a second look


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9.%]+", " ", s.lower()).strip()


def verify_evidence(ex: Extraction, doc: Document) -> bool:
    page = next((p for p in doc.pages if p.number == ex.page), None)
    if page is None or not ex.evidence:
        return False
    page_text, quote = _squash(page.text), _squash(ex.evidence)
    if quote not in page_text:
        # tolerate small extraction differences: 80 % of the quote's words on the page
        words = quote.split()
        if not words or sum(w in page_text for w in words) / len(words) < 0.8:
            return False
    # the number itself must also be on the page
    num = parse_number(ex.value) if ex.value is not None else None
    if num is not None and isinstance(ex.value, str) and re.search(r"\d", ex.value):
        digits = re.sub(r"[^\d.]", "", str(ex.value).split()[0]) if ex.value else ""
        flat_page = re.sub(r"[,\s]", "", page.text)
        return digits.rstrip(".") in flat_page
    return True


def normalise(ex: Extraction, f: Field) -> None:
    if ex.value is None:
        return
    if f.type == "text":
        ex.normalized_value = str(ex.value).strip()
        return
    if f.type == "boolean":
        ex.normalized_value = parse_bool(ex.value)
        if ex.normalized_value is None:
            ex.issues.append(f"could not read yes/no from '{ex.value}'")
        return
    if f.type == "percent":
        ex.normalized_value, ex.normalized_unit = parse_percent(ex.value), "%"
    else:
        ex.normalized_value = parse_number(ex.value)
    if ex.normalized_value is None:
        ex.issues.append(f"could not parse a number from '{ex.value}'")
        return
    if f.unit_family:
        converted, msg = convert(ex.normalized_value, ex.unit, f.unit_family)
        if msg:
            ex.issues.append(msg)
        if converted is not None:
            ex.normalized_value = round(converted, 4)
            ex.normalized_unit = f.target_unit
        else:
            ex.normalized_value = None
    elif f.type == "count":
        ex.normalized_unit = "count"
    elif f.type == "year":
        ex.normalized_unit = "year"


def field_rules(ex: Extraction, f: Field, doc: Document) -> None:
    v = ex.normalized_value
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return
    if v < 0:
        ex.issues.append("negative value")
    if f.type == "percent" and not 0 <= v <= 100:
        ex.issues.append("percentage outside 0-100")
    if f.id == "employee_turnover_rate" and v > 60:
        ex.issues.append("turnover above 60 % - unusual, check definition")
    if f.type == "count" and v != int(v):
        ex.issues.append("count is not a whole number")
    if f.type == "year" and not doc.year <= v <= 2100:
        ex.issues.append(f"target year {int(v)} is before the reporting year")
    prior = parse_number(ex.prior_year_value)
    if prior and f.type != "year":
        if f.type == "percent":
            prior = parse_percent(ex.prior_year_value)
        elif f.unit_family:
            prior, _ = convert(prior, ex.unit, f.unit_family)
        small_counts = f.type == "count" and max(abs(v), abs(prior or 0)) < 10
        if prior and not small_counts:
            change = abs(v - prior) / abs(prior)
            if change > YOY_THRESHOLD:
                ex.issues.append(f"year-on-year change of {change:.0%} vs prior year")


def _num(by_id: dict[str, Extraction], fid: str) -> Optional[float]:
    ex = by_id.get(fid)
    v = ex.normalized_value if ex else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def cross_field_rules(by_id: dict[str, Extraction]) -> None:
    def flag(fids, msg):
        for fid in fids:
            if fid in by_id and by_id[fid].value is not None:
                by_id[fid].issues.append(msg)

    s1, s3, total = _num(by_id, "scope1_emissions"), _num(by_id, "scope3_emissions"), _num(by_id, "total_ghg_emissions")
    s2s = [x for x in (_num(by_id, "scope2_market"), _num(by_id, "scope2_location")) if x is not None]
    if None not in (s1, s3, total) and s2s and total:
        if not any(abs(s1 + s2 + s3 - total) / total <= 0.05 for s2 in s2s):
            flag(["scope1_emissions", "scope3_emissions", "total_ghg_emissions", "scope2_market"],
                 "Scope 1+2+3 does not add up to reported total (>5 % gap)")

    wd, dis, con = _num(by_id, "water_withdrawal"), _num(by_id, "water_discharge"), _num(by_id, "water_consumption")
    if wd is not None and con is not None and con > wd * 1.01:
        flag(["water_consumption", "water_withdrawal"], "water consumption exceeds withdrawal")
    elif None not in (wd, dis, con) and wd:
        if abs((wd - dis) - con) / wd > 0.10:
            flag(["water_consumption"], "withdrawal - discharge differs from consumption by >10 %")

    board = _num(by_id, "board_size")
    for fid in ("independent_directors", "female_directors"):
        v = _num(by_id, fid)
        if board is not None and v is not None and v > board:
            flag([fid, "board_size"], f"{fid} ({int(v)}) exceeds board size ({int(board)})")


def validate(doc: Document, fields: list[Field], extractions: list[Extraction]) -> list[Extraction]:
    fmap = {f.id: f for f in fields}
    by_id = {e.field_id: e for e in extractions}
    for ex in extractions:
        f = fmap[ex.field_id]
        if ex.value is None:
            continue
        page = next((p for p in doc.pages if p.number == ex.page), None)
        if page is not None and page.ocr and f.type != "text":
            # OCR can drop a decimal point or swap 5/S - never auto-accept a number read from an image
            ex.issues.append("read from an image page (OCR) - check the digits")
        ex.evidence_verified = verify_evidence(ex, doc)
        if not ex.evidence_verified:
            ex.issues.append("evidence quote/value not found on cited page")
        normalise(ex, f)
        field_rules(ex, f, doc)
    cross_field_rules(by_id)

    for ex in extractions:
        if ex.value is None:
            ex.status = "missing"
        elif ex.confidence < CONFIDENCE_THRESHOLD:
            ex.issues.append(f"low confidence ({ex.confidence:.2f})")
            ex.status = "review"
        elif [i for i in ex.issues if not i.startswith("info")]:
            ex.status = "review"
        else:
            ex.status = "accepted"
    return extractions
