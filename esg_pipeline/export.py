"""Stage 7 - Export: analyst-friendly Excel workbook + JSON for downstream use."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .schema import Field
from .trends import build_trends, trends_rows

STATUS_FILL = {
    "accepted": PatternFill("solid", fgColor="E2F0D9"),
    "review": PatternFill("solid", fgColor="FFF2CC"),
    "missing": PatternFill("solid", fgColor="F2F2F2"),
}
HEADER_FILL = PatternFill("solid", fgColor="1F3864")


def to_rows(results: list[dict], fields: list[Field]) -> pd.DataFrame:
    fmap = {f.id: f for f in fields}
    rows = []
    for r in results:
        for ex in r["extractions"]:
            f = fmap[ex["field_id"]]
            rows.append({
                "company": r["company"], "year": r["year"], "category": f.category,
                "field_id": f.id, "data_point": f.label, "GRI": f.gri, "ESRS": f.esrs,
                "value_reported": ex["value"], "unit_reported": ex["unit"],
                "value_normalised": ex["normalized_value"], "unit_normalised": ex["normalized_unit"],
                "prior_year_reported": ex["prior_year_value"],
                "page": ex["page"], "evidence": ex["evidence"],
                "evidence_verified": ex["evidence_verified"],
                "confidence": round(ex["confidence"], 2), "method": ex["method"],
                "status": ex["status"], "issues": "; ".join(ex["issues"]),
                "source": r["source"],
            })
    return pd.DataFrame(rows)


def _style(ws, df: pd.DataFrame, status_col: str | None = "status") -> None:
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for i, col in enumerate(df.columns, start=1):
        width = min(max([len(str(col))] + [len(str(v)) for v in df[col].head(200)]) + 2, 60)
        ws.column_dimensions[get_column_letter(i)].width = width
    if status_col and status_col in df.columns:
        idx = list(df.columns).index(status_col)
        for row in ws.iter_rows(min_row=2):
            fill = STATUS_FILL.get(row[idx].value)
            if fill:
                for c in row:
                    c.fill = fill


def export(results: list[dict], fields: list[Field], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df = to_rows(results, fields)

    summary = (df.groupby(["company", "year", "status"]).size().unstack(fill_value=0)
                 .reindex(columns=["accepted", "review", "missing"], fill_value=0).reset_index())
    summary["auto_accept_rate"] = (summary["accepted"] / (summary[["accepted", "review", "missing"]].sum(axis=1))).round(2)

    wide = df.pivot_table(index=["company", "year"], columns="field_id", values="value_normalised",
                          aggfunc="first").reindex(columns=[f.id for f in fields]).reset_index()
    review = df[df["status"] == "review"][["company", "year", "field_id", "data_point", "value_reported",
                                           "unit_reported", "page", "evidence", "confidence", "issues"]]

    trend_rows, restatements = trends_rows(build_trends(results, fields), fields)
    trends = pd.DataFrame(trend_rows)
    restated = pd.DataFrame(restatements or [{"company": "", "label": "no restatements detected"}])

    with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
        sheets = {"Summary": (summary, None), "ESG_Data": (df, "status"),
                  "Company_View": (wide, None), "Review_Queue": (review, None),
                  "Trends": (trends, None), "Restatements": (restated, None)}
        for name, (frame, sc) in sheets.items():
            if frame.empty:
                frame = pd.DataFrame({"note": ["no data"]})
            frame.to_excel(xw, sheet_name=name, index=False)
            _style(xw.sheets[name], frame, sc)

    out_path.with_suffix(".json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return out_path
