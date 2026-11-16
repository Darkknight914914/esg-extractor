"""Stage 2 - Parse: turn a PDF into page-numbered text, with tables kept as rows.

Tables matter a lot in ESG reports (most KPIs live in data tables at the back),
so every table is re-emitted as pipe-separated rows under the page text. That
keeps label and value on the same line, which both extractors rely on.
Pages with almost no extractable text (scanned reports) fall back to OCR.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pdfplumber

from . import ocr as ocr_engine
from .schema import Document, Page

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 40          # below this a page is treated as scanned


def _table_to_text(table: list[list]) -> str:
    rows = []
    for row in table:
        cells = [(c or "").replace("\n", " ").strip() for c in row]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def tesseract_available() -> bool:          # kept for backwards compatibility
    return ocr_engine.engine() is not None


def parse_pdf(path: str | Path, company: str, year: int, ocr: bool = True) -> Document:
    pages: list[Page] = []
    with pdfplumber.open(str(path)) as pdf:
        for i, p in enumerate(pdf.pages, start=1):
            text = p.extract_text() or ""
            used_ocr = skipped = False
            if len(text.strip()) < MIN_TEXT_CHARS and ocr:
                if ocr_engine.engine():
                    try:
                        text = ocr_engine.read_page(p) or text
                        used_ocr = bool(text.strip())
                    except Exception as e:          # one bad page must not sink the report
                        log.warning("OCR failed on page %d: %s", i, e)
                        skipped = True
                else:
                    skipped = True
            tables = p.extract_tables() or []
            for n, t in enumerate(tables, start=1):
                text += f"\n[TABLE {n}]\n{_table_to_text(t)}"
            pages.append(Page(number=i, text=text, ocr=used_ocr, ocr_skipped=skipped))
    log.info("parsed %s: %d pages (%d OCR)", path, len(pages), sum(p.ocr for p in pages))
    return Document(company=company, year=year, source=str(path), pages=pages)
