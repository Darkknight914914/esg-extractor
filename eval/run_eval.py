"""Score pipeline output against hand-labelled gold answers.

  python eval/run_eval.py output/esg_rules.json [output/esg_llm.json ...]

Outcomes per field:
  correct       value matches gold (numbers within 1 %, after unit normalisation)
  correct_none  gold says "not disclosed" and nothing was extracted
  missed        disclosed, but nothing extracted (cheap - a human fills it)
  wrong         extracted value differs from gold, or invented when not disclosed

The metric that matters most is SILENT ERRORS: wrong values the pipeline
auto-accepted. Errors that land in the review queue are caught by a human;
silent ones end up in the dataset.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

GOLD_DIR = Path(__file__).parent / "gold"


def load_gold() -> dict[str, dict]:
    return {(g["company"], int(g["year"])): g for g in (json.loads(p.read_text()) for p in GOLD_DIR.glob("*.json"))}


def judge(gold, pred) -> str:
    if gold is None:
        return "correct_none" if pred is None else "wrong"
    if pred is None:
        return "missed"
    if gold == "TEXT":
        return "correct" if str(pred).strip() else "missed"
    if isinstance(gold, bool):
        return "correct" if pred is gold else "wrong"
    try:
        g, p = float(gold), float(pred)
    except (TypeError, ValueError):
        return "wrong"
    if g == 0:
        return "correct" if p == 0 else "wrong"
    return "correct" if abs(p - g) / abs(g) <= 0.01 else "wrong"


def score(path: str, gold: dict[str, dict]) -> dict:
    outcomes, silent, statuses, details = Counter(), [], Counter(), []
    for res in json.loads(Path(path).read_text()):
        g = gold.get((res["company"], int(res["year"])))
        if not g:
            continue
        for ex in res["extractions"]:
            fid = ex["field_id"]
            pred = ex["normalized_value"]
            out = judge(g["fields"].get(fid), pred)
            outcomes[out] += 1
            statuses[ex["status"]] += 1
            if out == "wrong":
                details.append(f"  {res['company'][:22]:22} {fid:30} got {pred!r:>14} want {g['fields'].get(fid)!r:>10} [{ex['status']}]")
                if ex["status"] == "accepted":
                    silent.append(fid)
    n = sum(outcomes.values())
    return {"file": path, "n": n, "outcomes": outcomes, "statuses": statuses,
            "accuracy": (outcomes["correct"] + outcomes["correct_none"]) / n if n else 0,
            "silent_errors": silent, "details": details}


def main(paths: list[str]) -> None:
    gold = load_gold()
    for p in paths:
        s = score(p, gold)
        o, st = s["outcomes"], s["statuses"]
        print(f"\n=== {s['file']} ({s['n']} data points)")
        print(f"accuracy           {s['accuracy']:.0%}")
        print(f"correct / none-ok  {o['correct']} / {o['correct_none']}")
        print(f"missed             {o['missed']}")
        print(f"wrong              {o['wrong']}")
        print(f"auto-accepted      {st['accepted']} ({st['accepted'] / s['n']:.0%})  review {st['review']}  missing {st['missing']}")
        print(f"SILENT ERRORS      {len(s['silent_errors'])} {s['silent_errors'] or ''}")
        if s["details"]:
            print("wrong values:")
            print("\n".join(s["details"]))


if __name__ == "__main__":
    main(sys.argv[1:] or ["output/esg_rules.json"])
