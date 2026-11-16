"""Stage 4 - LLM extractor (Claude via the Anthropic API).

Design choices that make this auditable rather than a black box:
  * Only the top-ranked pages per category are sent (cost + focus).
  * Output is forced through a tool schema -> always valid JSON.
  * Every value must come with the page number and a verbatim evidence quote;
    the validate stage checks the quote really exists on that page, which
    catches hallucinated numbers.
  * The model is told never to calculate - totals it adds up itself would be
    unauditable. Missing means null, not a guess.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Any, Optional

from .locate import PageIndex
from .schema import Document, Extraction, Field

log = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("ESG_MODEL", "claude-sonnet-5-5")
MAX_PAGES_PER_CALL = 8
MAX_CHARS_PER_PAGE = 7000

SYSTEM_PROMPT = """You are an ESG data analyst extracting disclosures from corporate reports.
Rules:
- Extract values for the REPORTING YEAR given, not prior years or targets.
- Copy numbers exactly as printed (keep thousands separators) and give the unit exactly as printed.
- `page` must be the page number from the [PAGE n] marker where the value appears.
- `evidence` must be a short VERBATIM quote (max ~25 words) copied from that page containing the value.
- Never calculate, sum or estimate. If a value is not explicitly disclosed, set found=false.
- If the prior-year value is shown next to it, put it in prior_year_value.
- For text fields, `value` is a 1-2 sentence factual summary of what the report says.
- For boolean fields, `value` is "true" or "false".
- confidence: 0.9+ clear table value; 0.6-0.8 prose or some ambiguity; below 0.5 unsure."""

TOOL = {
    "name": "record_esg_data",
    "description": "Record the extracted ESG data points.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field_id": {"type": "string"},
                        "found": {"type": "boolean"},
                        "value": {"type": ["string", "null"]},
                        "unit": {"type": ["string", "null"]},
                        "prior_year_value": {"type": ["string", "null"]},
                        "page": {"type": ["integer", "null"]},
                        "evidence": {"type": ["string", "null"]},
                        "confidence": {"type": "number"},
                    },
                    "required": ["field_id", "found", "value", "page", "evidence", "confidence"],
                },
            }
        },
        "required": ["results"],
    },
}


def _field_brief(f: Field) -> str:
    unit = f" (expected unit family: {f.unit_family})" if f.unit_family else ""
    return f"- {f.id}: {f.label} [type={f.type}; GRI {f.gri}]{unit}"


def build_prompt(doc: Document, fields: list[Field], page_numbers: list[int]) -> str:
    pages = {p.number: p for p in doc.pages}
    context = "\n\n".join(
        f"[PAGE {n}]\n{pages[n].text[:MAX_CHARS_PER_PAGE]}" for n in sorted(page_numbers)
    )
    field_list = "\n".join(_field_brief(f) for f in fields)
    return (
        f"Company: {doc.company}\nReporting year: {doc.year}\n\n"
        f"Fields to extract:\n{field_list}\n\n"
        f"Report pages:\n{context}\n\n"
        "Call record_esg_data with one entry per field above."
    )


class LLMExtractor:
    def __init__(self, client: Any = None, model: str = DEFAULT_MODEL):
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise SystemExit("pip install anthropic  (and set ANTHROPIC_API_KEY)") from e
            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def _call(self, prompt: str) -> list[dict]:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": TOOL["name"]},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                data = block.input if isinstance(block.input, dict) else json.loads(block.input)
                return data.get("results", [])
        return []

    def extract_document(self, doc: Document, fields: list[Field]) -> list[Extraction]:
        index = PageIndex(doc)
        by_cat: dict[str, list[Field]] = defaultdict(list)
        for f in fields:
            by_cat[f.category].append(f)

        results: dict[str, dict] = {}
        for cat, cat_fields in by_cat.items():
            # union of each field's best pages, ranked by how many fields want them
            votes: dict[int, int] = defaultdict(int)
            for f in cat_fields:
                for rank, n in enumerate(index.top_pages(f, k=3)):
                    votes[n] += 3 - rank
            pages = [n for n, _ in sorted(votes.items(), key=lambda kv: -kv[1])][:MAX_PAGES_PER_CALL]
            if not pages:
                continue
            log.info("%s / %s: %d fields over pages %s", doc.company, cat, len(cat_fields), sorted(pages))
            for r in self._call(build_prompt(doc, cat_fields, pages)):
                results[r.get("field_id", "")] = r

        out = []
        for f in fields:
            r: Optional[dict] = results.get(f.id)
            ex = Extraction(field_id=f.id, method="llm", year=doc.year)
            if r and r.get("found") and r.get("value") not in (None, ""):
                ex.value = r["value"]
                ex.unit = r.get("unit")
                ex.prior_year_value = r.get("prior_year_value")
                ex.page = r.get("page")
                ex.evidence = r.get("evidence") or ""
                ex.confidence = float(r.get("confidence", 0.5))
            out.append(ex)
        return out
