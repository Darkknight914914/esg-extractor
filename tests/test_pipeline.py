import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from esg_pipeline.extract_llm import LLMExtractor  # noqa: E402
from esg_pipeline.extract_rules import extract_document  # noqa: E402
from esg_pipeline.normalize import convert, parse_number, parse_percent  # noqa: E402
from esg_pipeline.parse import parse_pdf  # noqa: E402
from esg_pipeline.schema import Extraction, load_schema  # noqa: E402
from esg_pipeline.validate import validate  # noqa: E402

SAMPLES = ROOT / "samples"
FIELDS = load_schema()


@pytest.fixture(scope="session", autouse=True)
def samples():
    if not (SAMPLES / "nordwind_2025.pdf").exists():
        subprocess.run([sys.executable, str(SAMPLES / "make_samples.py")], check=True)


@pytest.fixture(scope="session")
def nordwind():
    return parse_pdf(SAMPLES / "nordwind_2025.pdf", "Nordwind Logistics AG", 2025)


@pytest.fixture(scope="session")
def helios():
    return parse_pdf(SAMPLES / "helios_2025.pdf", "Helios Components plc", 2025)


# ---------- normalisation ----------
@pytest.mark.parametrize("raw,expected", [("1,234.5", 1234.5), ("12 345", 12345), ("(1,200)", -1200),
                                          ("n/a", None), (42, 42.0)])
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


def test_units():
    assert convert(21.1, "ktCO2e", "emissions")[0] == pytest.approx(21100)
    assert convert(185400, "m3", "water")[0] == pytest.approx(185.4)
    assert convert(5, "million m³", "water")[0] == pytest.approx(5000)
    assert convert(1, "furlongs", "water")[0] is None
    assert parse_percent("0.32") == 32


# ---------- rules extractor ----------
def test_rules_reads_correct_year_column(helios):
    # Helios tables list 2024 BEFORE 2025 - must still pick 2025
    by_id = {e.field_id: e for e in extract_document(helios, FIELDS)}
    assert parse_number(by_id["scope1_emissions"].value) == 21.1
    assert parse_number(by_id["water_withdrawal"].value) == 598


def test_ocr_page_is_read(helios):
    assert helios.pages[-1].ocr
    assert "directors" in helios.pages[-1].text.lower()


# ---------- validation ----------
def test_scope_total_mismatch_is_flagged(helios):
    ex = validate(helios, FIELDS, extract_document(helios, FIELDS))
    total = next(e for e in ex if e.field_id == "total_ghg_emissions")
    assert total.status == "review"
    assert any("does not add up" in i for i in total.issues)


def test_consistent_report_is_accepted(nordwind):
    ex = {e.field_id: e for e in validate(nordwind, FIELDS, extract_document(nordwind, FIELDS))}
    assert ex["total_ghg_emissions"].status == "accepted"
    assert ex["water_consumption"].normalized_value == pytest.approx(43.1)


def test_board_arithmetic(nordwind):
    exs = [Extraction("board_size", value="5", page=4, evidence="Supervisory board members", confidence=0.9),
           Extraction("independent_directors", value="8", page=4, evidence="Independent directors", confidence=0.9)]
    out = validate(nordwind, [f for f in FIELDS if f.id in ("board_size", "independent_directors")], exs)
    assert all(e.status == "review" for e in out)


# ---------- LLM extractor with a fake client (no API call) ----------
class FakeClient:
    """Pretends to be Claude: one honest answer, one hallucinated one."""
    def __init__(self):
        self.messages = self
        self.prompts = []

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        results = [
            {"field_id": "scope1_emissions", "found": True, "value": "48,210", "unit": "tCO2e",
             "page": 2, "evidence": "Scope 1 emissions | tCO2e | 48,210 | 52,980", "confidence": 0.95,
             "prior_year_value": "52,980"},
            {"field_id": "nox_emissions", "found": True, "value": "999", "unit": "t",
             "page": 2, "evidence": "NOx emissions totalled 999 tonnes", "confidence": 0.9},
        ]
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"results": results})])


def test_llm_extractor_and_hallucination_guard(nordwind):
    client = FakeClient()
    fields = [f for f in FIELDS if f.id in ("scope1_emissions", "nox_emissions")]
    ex = {e.field_id: e for e in validate(nordwind, fields, LLMExtractor(client=client).extract_document(nordwind, fields))}
    assert ex["scope1_emissions"].status == "accepted"
    assert ex["nox_emissions"].status == "review"                 # invented quote caught
    assert ex["nox_emissions"].evidence_verified is False
    assert "[PAGE 2]" in client.prompts[0]                         # locator sent the right page


# ---------- multi-year trends ----------
def test_trends_and_restatements():
    from esg_pipeline.pipeline import run_batch
    from esg_pipeline.trends import build_trends
    results = run_batch(SAMPLES / "manifest.csv", FIELDS)
    t = build_trends(results, FIELDS)["nordwind logistics ag"]
    assert t["years"] == [2023, 2024, 2025]           # 2023 comes from the 2024 report's prior-year column
    restated = {(r["field_id"], r["year"]) for r in t["restatements"]}
    assert ("scope3_emissions", 2024) in restated and ("ltifr", 2024) in restated
    s1 = t["series"]["scope1_emissions"]
    assert s1["direction"] == "improved" and s1["change_pct"] == pytest.approx(-9.0, abs=0.1)
    assert t["series"]["scope3_emissions"]["points"][2024]["value"] == 430100   # restated value wins


# ---------- OCR ----------
def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_boxes_to_text_rebuilds_table_rows():
    from esg_pipeline.ocr import boxes_to_text
    items = [(_box(300, 100, 360, 120), "tCO2e"), (_box(10, 102, 150, 121), "Scope 1 emissions"),
             (_box(500, 101, 560, 119), "48,210"), (_box(650, 100, 710, 120), "52,980"),
             (_box(10, 140, 60, 160), "Water"), (_box(66, 141, 140, 159), "withdrawal")]
    assert boxes_to_text(items).splitlines() == ["Scope 1 emissions | tCO2e | 48,210 | 52,980", "Water withdrawal"]


@pytest.mark.parametrize("fmt", ["v1", "v2"])
def test_rapidocr_engine_both_package_versions(monkeypatch, fmt):
    import numpy as np
    from esg_pipeline import ocr
    rows = [(_box(10, 10, 120, 30), "Independent"), (_box(126, 10, 220, 30), "directors"), (_box(400, 10, 420, 30), "4")]

    def fake_engine(img):
        assert isinstance(img, np.ndarray)
        if fmt == "v1":
            return [[b, t, 0.99] for b, t in rows], 0.1
        return SimpleNamespace(boxes=np.array([b for b, _ in rows]), txts=tuple(t for _, t in rows))
    monkeypatch.setattr(ocr, "_ENGINE", "rapidocr")
    monkeypatch.setattr(ocr, "_RAPID", fake_engine)
    with __import__("pdfplumber").open(str(SAMPLES / "helios_2025.pdf")) as pdf:
        assert ocr.read_page(pdf.pages[-1]) == "Independent directors | 4"


def test_ocr_values_always_reviewed(helios):
    ex = validate(helios, FIELDS, extract_document(helios, FIELDS))
    ocr_pages = {p.number for p in helios.pages if p.ocr}
    for e in ex:
        if e.page in ocr_pages and e.value is not None:
            assert e.status == "review"
