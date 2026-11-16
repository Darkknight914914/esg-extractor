"""Schema loading and the core data structures shared by every pipeline stage."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

import yaml

DEFAULT_SCHEMA = Path(__file__).resolve().parent.parent / "config" / "schema.yaml"


@dataclass
class Field:
    id: str
    category: str            # E / S / G
    label: str
    type: str                # number | count | percent | boolean | year | text
    keywords: list[str]
    gri: str = ""
    esrs: str = ""
    unit_family: Optional[str] = None
    target_unit: Optional[str] = None
    patterns: list[str] = field(default_factory=list)
    better: Optional[str] = None     # lower | higher, for trend direction


@dataclass
class Page:
    number: int              # 1-based page number as printed by a PDF viewer
    text: str
    ocr: bool = False
    ocr_skipped: bool = False    # image-only page, but no OCR engine available


@dataclass
class Document:
    company: str
    year: int
    source: str
    pages: list[Page]


@dataclass
class Extraction:
    """One extracted data point, with everything needed to audit it."""
    field_id: str
    value: Any = None                    # raw value as reported
    unit: Optional[str] = None           # raw unit as reported
    year: Optional[int] = None
    prior_year_value: Any = None         # used for year-on-year checks
    page: Optional[int] = None
    evidence: str = ""                   # verbatim snippet from the report
    confidence: float = 0.0
    method: str = ""                     # "rules" | "llm"
    # filled in by normalise / validate stages
    normalized_value: Any = None
    normalized_unit: Optional[str] = None
    evidence_verified: Optional[bool] = None
    issues: list[str] = field(default_factory=list)
    status: str = "pending"              # accepted | review | missing

    def to_dict(self) -> dict:
        return asdict(self)


def load_schema(path: str | Path = DEFAULT_SCHEMA) -> list[Field]:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return [Field(**item) for item in raw["fields"]]
