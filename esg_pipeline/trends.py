"""Multi-year comparison: build a time series per company and KPI.

Sources for a year's value, in order of preference:
  1. "reported"  - the value from that year's own report
  2. "prior-year column" - the comparative figure printed in the NEXT year's
     report (most reports show last year next to this year), so even one
     report yields two years of data.

When both exist and differ by more than 1 %, the company has RESTATED the
figure (changed methodology, corrected an error, changed scope). Analysts
need to know this: the latest restated value is used for the trend, and the
restatement is listed separately.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Optional

from .normalize import convert, parse_number, parse_percent
from .schema import Field

RESTATEMENT_TOLERANCE = 0.01
FLAT_TOLERANCE = 0.01


def company_key(name: str) -> str:
    """'Nordwind Logistics AG' and 'nordwind logistics ag ' are the same company."""
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _norm_prior(ex: dict, f: Field) -> Optional[float]:
    raw = ex.get("prior_year_value")
    if raw in (None, ""):
        return None
    if f.type == "percent":
        return parse_percent(raw)
    v = parse_number(raw)
    if v is None:
        return None
    if f.unit_family:
        v, _ = convert(v, ex.get("unit"), f.unit_family)
    return v


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def build_trends(results: list[dict], fields: list[Field]) -> dict:
    """Return {company_key: {"company", "years", "series", "restatements"}}."""
    quant = [f for f in fields if f.type in ("number", "count", "percent")]
    fmap = {f.id: f for f in quant}
    reported: dict = defaultdict(dict)    # (co, fid) -> {year: point}
    prior: dict = defaultdict(dict)       # (co, fid) -> {year: point}
    names: dict[str, str] = {}

    for r in results:
        co = company_key(r["company"])
        names.setdefault(co, r["company"])
        year = int(r["year"])
        for ex in r["extractions"]:
            f = fmap.get(ex["field_id"])
            if not f:
                continue
            if _is_num(ex.get("normalized_value")) and ex.get("status") != "missing":
                reported[(co, f.id)][year] = {"value": ex["normalized_value"], "source": "reported",
                                              "report_year": year, "status": ex["status"], "page": ex.get("page")}
            pv = _norm_prior(ex, f)
            if pv is not None and ex.get("normalized_value") is not None:
                prior[(co, f.id)][year - 1] = {"value": pv, "source": "prior-year column",
                                               "report_year": year, "status": ex["status"], "page": ex.get("page")}

    out: dict = {}
    for co, name in names.items():
        series, restatements, years = {}, [], set()
        for f in quant:
            rep, pri = reported.get((co, f.id), {}), prior.get((co, f.id), {})
            points = {}
            for y in sorted(set(rep) | set(pri)):
                a, b = rep.get(y), pri.get(y)
                if a and b and a["value"] and abs(b["value"] - a["value"]) / abs(a["value"]) > RESTATEMENT_TOLERANCE:
                    restatements.append({
                        "field_id": f.id, "label": f.label, "year": y,
                        "originally_reported": a["value"], "restated_as": b["value"],
                        "unit": f.target_unit or ("%" if f.type == "percent" else ""),
                        "change_pct": round((b["value"] - a["value"]) / abs(a["value"]) * 100, 1),
                        "restated_in": b["report_year"]})
                    points[y] = {**b, "restated": True, "original": a["value"]}
                else:
                    points[y] = a or b
            if not points:
                continue
            years |= set(points)
            ys = sorted(points)
            entry = {"points": points, "unit": f.target_unit or ("%" if f.type == "percent" else ""),
                     "better": f.better}
            if len(ys) >= 2:
                cur, prev, first = points[ys[-1]]["value"], points[ys[-2]]["value"], points[ys[0]]["value"]
                entry["change_abs"] = cur - prev
                entry["change_pct"] = round((cur - prev) / abs(prev) * 100, 1) if prev else None
                entry["change_since_first_pct"] = round((cur - first) / abs(first) * 100, 1) if first else None
                entry["direction"] = assess(cur, prev, f.better)
            series[f.id] = entry
        out[co] = {"company": name, "years": sorted(years), "series": series, "restatements": restatements}
    return out


def assess(cur: float, prev: float, better: Optional[str]) -> str:
    if prev == 0 and cur == 0:
        return "flat"
    base = abs(prev) if prev else abs(cur)
    if abs(cur - prev) / base <= FLAT_TOLERANCE:
        return "flat"
    if not better:
        return "up" if cur > prev else "down"
    improved = (cur < prev) if better == "lower" else (cur > prev)
    return "improved" if improved else "worsened"


def trends_rows(trends: dict, fields: list[Field]) -> tuple[list[dict], list[dict]]:
    """Flatten for Excel: (long-format time series rows, restatement rows)."""
    labels = {f.id: f.label for f in fields}
    rows, rest = [], []
    for t in trends.values():
        for fid, s in t["series"].items():
            for y, p in sorted(s["points"].items()):
                rows.append({"company": t["company"], "field_id": fid, "data_point": labels[fid], "year": y,
                             "value": p["value"], "unit": s["unit"], "source": p["source"],
                             "from_report_year": p["report_year"], "restated": p.get("restated", False),
                             "change_vs_prev_year_pct": s.get("change_pct") if y == max(s["points"]) else None,
                             "assessment": s.get("direction") if y == max(s["points"]) else None})
        for r in t["restatements"]:
            rest.append({"company": t["company"], **r})
    return rows, rest
