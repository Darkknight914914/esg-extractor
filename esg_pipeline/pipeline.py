"""Orchestrates parse -> locate -> extract -> validate for a batch of reports."""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from .extract_rules import extract_document as rules_extract
from .parse import parse_pdf
from .schema import Field
from .validate import validate

log = logging.getLogger(__name__)


def read_manifest(path: str | Path) -> list[dict]:
    """CSV with columns: company, year, pdf  (pdf path relative to the manifest)."""
    path = Path(path)
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["year"] = int(r["year"])
        pdf = Path(r["pdf"])
        r["pdf"] = str(pdf if pdf.is_absolute() else path.parent / pdf)
    return rows


def run_one(company: str, year: int, pdf: str, fields: list[Field], extractor: str = "rules",
            llm=None) -> dict:
    doc = parse_pdf(pdf, company, year)
    if extractor == "llm":
        extractions = llm.extract_document(doc, fields)
    elif extractor == "hybrid":
        # rules first (free, deterministic); LLM fills whatever rules missed or doubted
        extractions = validate(doc, fields, rules_extract(doc, fields))
        need = [f for f, e in zip(fields, extractions) if e.status != "accepted"]
        if need:
            llm_by_id = {e.field_id: e for e in llm.extract_document(doc, need)}
            extractions = [llm_by_id.get(e.field_id, e) if e.status != "accepted" else e
                           for e in extractions]
            for e in extractions:          # reset so validation runs cleanly once more
                e.issues, e.status = [], "pending"
    else:
        extractions = rules_extract(doc, fields)
    extractions = validate(doc, fields, extractions)
    counts = {s: sum(e.status == s for e in extractions) for s in ("accepted", "review", "missing")}
    log.info("%s %s: %s", company, year, counts)
    return {"company": company, "year": year, "source": pdf,
            "extractions": [e.to_dict() for e in extractions]}


def run_batch(manifest: str | Path, fields: list[Field], extractor: str = "rules", llm=None) -> list[dict]:
    return [run_one(r["company"], r["year"], r["pdf"], fields, extractor, llm) for r in read_manifest(manifest)]
