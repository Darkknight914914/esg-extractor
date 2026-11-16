"""Stage 4 (baseline) - Rule-based extractor.

Regex anchors + table-structure heuristics. No API key needed, fully
deterministic, and it gives the LLM extractor something to be measured
against in the eval. It only handles quantitative fields; qualitative ones
(policies, committees) are left for the LLM or a human.
"""
from __future__ import annotations

import re
from typing import Optional

from .locate import PageIndex
from .normalize import NUM_RE, UNIT_TABLES, parse_number
from .schema import Document, Extraction, Field

YEAR_CELL = re.compile(r"^(?:fy\s*)?(20\d\d)(?:/\d\d)?$", re.I)
QUANT_TYPES = {"number", "count", "percent", "year"}


def _is_year_header(cells: list[str]) -> dict[int, int]:
    """Map column index -> year if this row looks like a header ('Metric | Unit | 2025 | 2024')."""
    cols = {}
    for i, c in enumerate(cells):
        m = YEAR_CELL.match(c.strip())
        if m:
            cols[i] = int(m.group(1))
    return cols if len(cols) >= 1 and len(cols) >= sum(bool(NUM_RE.search(c)) for c in cells) else {}


def _unit_from_text(text: str, field: Field) -> Optional[str]:
    t = text.lower()
    if field.type == "percent" and "%" in t:
        return "%"
    if not field.unit_family:
        return None
    best = None
    for key in UNIT_TABLES[field.unit_family]:
        if re.search(r"(?<![a-z])" + re.escape(key) + r"(?![a-z0-9])", t):
            if best is None or len(key) > len(best):
                best = key
    return best


def _numeric_cells(cells: list[str], start: int) -> list[tuple[int, str]]:
    out = []
    for i in range(start, len(cells)):
        c = cells[i].strip()
        if c and NUM_RE.fullmatch(c.replace("%", "").strip().strip("()")):
            out.append((i, c))
    return out


def _from_table_line(line, header, field, doc_year, rx):
    cells = [c.strip() for c in line.split("|")]
    label_idx = next((i for i, c in enumerate(cells) if rx.search(c)), None)
    if label_idx is None:
        return None
    nums = _numeric_cells(cells, label_idx + 1)
    if not nums:
        return None
    unit = None
    for c in cells[label_idx:]:
        if not NUM_RE.fullmatch(c.replace("%", "").strip()) and not unit:
            key = _unit_from_text(c, field)
            if key:   # keep the unit as printed when the cell is just the unit
                unit = c.strip() if c.strip().lower() == key else key
    if field.type == "percent" and any("%" in c for _, c in nums):
        unit = "%"
    value = prior = None
    conf = 0.6
    if header:
        by_year = {header[i]: c for i, c in nums if i in header}
        if doc_year in by_year:
            value, conf = by_year[doc_year], 0.85
            prior = by_year.get(doc_year - 1)
    if value is None:
        value = nums[0][1]
        prior = nums[1][1] if len(nums) > 1 else None
    return value, unit, prior, conf


def _from_prose_line(line, field, rx):
    m = rx.search(line)
    if not m:
        return None
    if field.type == "year":
        y = re.search(r"(20\d\d)", line[m.start():])
        return (y.group(1), None, None, 0.6) if y else None
    tail = line[m.end(): m.end() + 100]
    for n in NUM_RE.finditer(tail):
        token = n.group(0)
        after = tail[n.end(): n.end() + 25]
        if YEAR_CELL.match(token):           # skip years like "by 2030"
            continue
        if after.lstrip().startswith("%") and field.type != "percent":
            continue                          # "reduced by 8%" is not the KPI
        before = tail[max(0, n.start() - 12): n.start()]
        unit = "%" if after.lstrip().startswith("%") else (
            _unit_from_text(after, field) or _unit_from_text(before, field))
        return token, unit, None, 0.5
    return None


def extract_field(doc: Document, index: PageIndex, field: Field) -> Extraction:
    ex = Extraction(field_id=field.id, method="rules", year=doc.year)
    if field.type not in QUANT_TYPES or not field.patterns:
        return ex
    candidates = index.top_pages(field, k=4)
    order = candidates + [p.number for p in doc.pages if p.number not in candidates]
    pages = {p.number: p for p in doc.pages}
    regexes = [re.compile(p, re.I) for p in field.patterns]

    # pass 1: tables (most reliable), pass 2: prose
    for pass_ in ("table", "prose"):
        for num in order:
            header: dict[int, int] = {}
            text = pages[num].text
            if pass_ == "table":
                units = text.splitlines()
            else:   # prose: rebuild sentences so wrapped lines don't split label and value
                body = " ".join(l for l in text.splitlines() if "|" not in l and not l.startswith("[TABLE"))
                units = re.split(r"(?<=[.!?])\s+(?=[A-Z])", body)
            for line in units:
                if pass_ == "table":
                    if "|" not in line:
                        header = {} if line.startswith("[TABLE") else header
                        continue
                    h = _is_year_header([c.strip() for c in line.split("|")])
                    if h:
                        header = h
                        continue
                    hit = next((r for r in (_from_table_line(line, header, field, doc.year, rx)
                                            for rx in regexes) if r), None)
                else:
                    if "|" in line:
                        continue
                    hit = next((r for r in (_from_prose_line(line, field, rx) for rx in regexes) if r), None)
                if hit:
                    value, unit, prior, conf = hit
                    ex.value = value
                    ex.unit = unit
                    ex.prior_year_value = prior
                    ex.page = num
                    ex.evidence = line.strip()[:300]
                    ex.confidence = conf if num in candidates else conf - 0.1
                    return ex
    return ex


def extract_document(doc: Document, fields: list[Field]) -> list[Extraction]:
    index = PageIndex(doc)
    return [extract_field(doc, index, f) for f in fields]
