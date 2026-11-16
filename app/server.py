"""ESG Extractor dashboard - upload reports, run the pipeline, review, export.

  python app/server.py            ->  http://localhost:8000

Runs are stored under runs/<run_id>/ (uploaded PDFs + results.json), so they
survive a restart. The pipeline runs in a background thread and the page polls
its progress, stage by stage.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from esg_pipeline.export import export  # noqa: E402
from esg_pipeline.extract_rules import extract_document as rules_extract  # noqa: E402
from esg_pipeline.locate import PageIndex  # noqa: E402
from esg_pipeline.parse import parse_pdf  # noqa: E402
from esg_pipeline.schema import Extraction, load_schema  # noqa: E402
from esg_pipeline.trends import build_trends  # noqa: E402
from esg_pipeline.validate import normalise, validate  # noqa: E402

RUNS_DIR = Path(os.environ.get("ESG_RUNS_DIR", ROOT / "runs"))
SAMPLES = ROOT / "samples"
FIELDS = load_schema()
FMAP = {f.id: f for f in FIELDS}
STAGES = ["queued", "parsing", "locating", "extracting", "validating", "done"]

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
LOCK = threading.Lock()


# ---------------------------------------------------------------- storage
def run_dir(run_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{8}", run_id):
        abort(404)
    d = RUNS_DIR / run_id
    if not d.exists():
        abort(404)
    return d


def load_run(run_id: str) -> dict:
    return json.loads((run_dir(run_id) / "run.json").read_text())


def save_run(run: dict) -> None:
    d = RUNS_DIR / run["id"]
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "run.json.tmp"
    tmp.write_text(json.dumps(run, indent=2, default=str))
    tmp.replace(d / "run.json")


def llm_available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------- pipeline job
def _set(run_id: str, i: int, **kw) -> None:
    """Every write goes through disk under one lock, so the background job,
    reviewer actions and 'add reports' never overwrite each other."""
    with LOCK:
        run = load_run(run_id)
        run["docs"][i].update(kw)
        save_run(run)


def execute(run: dict) -> None:
    llm = None
    if run["extractor"] in ("llm", "hybrid"):
        from esg_pipeline.extract_llm import LLMExtractor
        llm = LLMExtractor()
    rid = run["id"]
    d = RUNS_DIR / rid
    for i, doc_meta in enumerate(run["docs"]):
        if doc_meta["stage"] != "queued":      # already processed in an earlier pass
            continue
        try:
            t0 = time.time()
            _set(rid, i, stage="parsing")
            doc = parse_pdf(d / doc_meta["file"], doc_meta["company"], int(doc_meta["year"]))
            _set(rid, i, stage="locating", pages=len(doc.pages), ocr_pages=sum(p.ocr for p in doc.pages),
                 ocr_skipped=sum(p.ocr_skipped for p in doc.pages), error=None)
            index = PageIndex(doc)
            located = {f.id: index.top_pages(f, k=3) for f in FIELDS}
            _set(rid, i, stage="extracting")
            if run["extractor"] == "llm":
                ex = llm.extract_document(doc, FIELDS)
            elif run["extractor"] == "hybrid":
                ex = validate(doc, FIELDS, rules_extract(doc, FIELDS))
                need = [f for f, e in zip(FIELDS, ex) if e.status != "accepted"]
                got = {e.field_id: e for e in llm.extract_document(doc, need)} if need else {}
                ex = [got.get(e.field_id, e) if e.status != "accepted" else e for e in ex]
                for e in ex:
                    e.issues, e.status = [], "pending"
            else:
                ex = rules_extract(doc, FIELDS)
            _set(rid, i, stage="validating")
            ex = validate(doc, FIELDS, ex)
            items = []
            for e in ex:
                item = e.to_dict()
                item["candidate_pages"] = located[e.field_id]
                item["reviewed"] = False
                items.append(item)
            _set(rid, i, stage="done", extractions=items, seconds=round(time.time() - t0, 1))
        except Exception as err:  # keep going with the other reports
            _set(rid, i, stage="error", error=f"{type(err).__name__}: {err}")
    with LOCK:
        run = load_run(rid)
        run["status"] = "done"
        run["finished"] = datetime.now().isoformat(timespec="seconds")
        save_run(run)


def _store_files(d: Path, files: list, meta: list[dict], existing: set[str]) -> list[dict]:
    docs = []
    for (name, src), m in zip(files, meta):
        fname = secure_filename(name) or "report.pdf"
        stem, n = Path(fname).stem, 1
        while fname in existing:                     # never overwrite an earlier upload
            fname, n = f"{stem}_{n}.pdf", n + 1
        existing.add(fname)
        if isinstance(src, Path):
            shutil.copy(src, d / fname)
        else:
            src.save(d / fname)
        docs.append({"file": fname, "company": m["company"], "year": int(m["year"]), "stage": "queued"})
    return docs


def start_run(files: list, meta: list[dict], extractor: str) -> dict:
    run_id = uuid.uuid4().hex[:8]
    d = RUNS_DIR / run_id
    d.mkdir(parents=True)
    run = {"id": run_id, "created": datetime.now().isoformat(timespec="seconds"),
           "extractor": extractor, "status": "running", "docs": _store_files(d, files, meta, set())}
    save_run(run)
    threading.Thread(target=execute, args=(run,), daemon=True).start()
    return run


def _parse_uploads():
    """Read files[] + company[] + year[] from a multipart form. Returns (files, meta) or an error response."""
    uploads = request.files.getlist("files")
    companies, years = request.form.getlist("company"), request.form.getlist("year")
    if not uploads:
        return None, (jsonify({"error": "No files uploaded"}), 400)
    files, meta = [], []
    for f, c, y in zip(uploads, companies, years):
        if not f.filename.lower().endswith(".pdf"):
            return None, (jsonify({"error": f"{f.filename} is not a PDF"}), 400)
        if not c.strip() or not str(y).isdigit():
            return None, (jsonify({"error": f"Company and year required for {f.filename}"}), 400)
        files.append((f.filename, f))
        meta.append({"company": c.strip(), "year": int(y)})
    return (files, meta), None


# ---------------------------------------------------------------- routes
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def config():
    return jsonify({
        "llm_available": llm_available(),
        "stages": STAGES,
        "fields": [asdict(f) for f in FIELDS],
    })


@app.get("/api/runs")
def list_runs():
    runs = []
    if RUNS_DIR.exists():
        for p in RUNS_DIR.glob("*/run.json"):
            r = json.loads(p.read_text())
            summary = {"accepted": 0, "review": 0, "missing": 0}
            for d in r["docs"]:
                for e in d.get("extractions", []):
                    summary[e["status"]] = summary.get(e["status"], 0) + 1
            runs.append({"id": r["id"], "created": r["created"], "status": r["status"],
                         "extractor": r["extractor"], "companies": [d["company"] for d in r["docs"]],
                         "summary": summary})
    runs.sort(key=lambda r: r["created"], reverse=True)
    return jsonify(runs)


@app.post("/api/runs")
def create_run():
    body = request.get_json(silent=True) or {}
    extractor = (body.get("extractor") if request.is_json else request.form.get("extractor")) or "rules"
    if extractor not in ("rules", "llm", "hybrid"):
        abort(400, "unknown extractor")
    if extractor != "rules" and not llm_available():
        return jsonify({"error": "Set ANTHROPIC_API_KEY and pip install anthropic to use Claude."}), 400

    if body.get("sample"):
        manifest = [l.split(",") for l in (SAMPLES / "manifest.csv").read_text().strip().splitlines()[1:]]
        files = [(pdf, SAMPLES / pdf) for _, _, pdf in manifest]
        meta = [{"company": c, "year": y} for c, y, _ in manifest]
        if not all(p.exists() for _, p in files):
            return jsonify({"error": "Sample PDFs missing - run python samples/make_samples.py"}), 400
    else:
        parsed, err = _parse_uploads()
        if err:
            return err
        files, meta = parsed
    run = start_run(files, meta, extractor)
    return jsonify({"id": run["id"]}), 201


@app.get("/api/runs/<run_id>")
def get_run(run_id):
    with LOCK:
        return jsonify(load_run(run_id))


@app.post("/api/runs/<run_id>/docs")
def add_docs(run_id):
    """Add more reports (e.g. earlier years) to an existing run and process only those."""
    d = run_dir(run_id)
    parsed, err = _parse_uploads()
    if err:
        return err
    with LOCK:
        run = load_run(run_id)
        if run["status"] == "running":
            return jsonify({"error": "This run is still processing - wait until it finishes."}), 409
        existing = {doc["file"] for doc in run["docs"]}
        run["docs"] += _store_files(d, *parsed, existing)
        run["status"] = "running"
        save_run(run)
    threading.Thread(target=execute, args=(run,), daemon=True).start()
    return jsonify({"id": run_id, "docs": len(run["docs"])}), 201


@app.post("/api/runs/<run_id>/retry")
def retry(run_id):
    """Re-process reports that failed (e.g. after installing Tesseract)."""
    with LOCK:
        run = load_run(run_id)
        if run["status"] == "running":
            return jsonify({"error": "This run is still processing."}), 409
        failed = [d for d in run["docs"] if d["stage"] == "error"]
        if not failed:
            return jsonify({"error": "Nothing to retry."}), 400
        for d in failed:
            d.update(stage="queued", error=None)
        run["status"] = "running"
        save_run(run)
    threading.Thread(target=execute, args=(run,), daemon=True).start()
    return jsonify({"retrying": len(failed)})


@app.get("/api/runs/<run_id>/trends")
def trends(run_id):
    with LOCK:
        run = load_run(run_id)
    return jsonify(build_trends(_results(run), FIELDS))


@app.post("/api/runs/<run_id>/review")
def review(run_id):
    body = request.get_json(force=True)
    action, fid, doc_i = body.get("action"), body.get("field_id"), int(body.get("doc", -1))
    if action not in ("accept", "edit", "reject") or fid not in FMAP:
        abort(400)
    with LOCK:
        run = load_run(run_id)
        if not 0 <= doc_i < len(run["docs"]):
            abort(400)
        items = run["docs"][doc_i].get("extractions", [])
        item = next((e for e in items if e["field_id"] == fid), None)
        if item is None:
            abort(404)
        stamp = f"{datetime.now():%Y-%m-%d %H:%M}"
        if action == "accept":
            item.update(status="accepted", issues=[f"approved by reviewer {stamp}"])
        elif action == "reject":
            item.update(value=None, normalized_value=None, unit=None, status="missing",
                        issues=[f"marked not disclosed by reviewer {stamp}"])
        else:
            ex = Extraction(field_id=fid, value=body.get("value"), unit=body.get("unit") or None)
            normalise(ex, FMAP[fid])
            item.update(value=ex.value, unit=ex.unit, normalized_value=ex.normalized_value,
                        normalized_unit=ex.normalized_unit, method="human", confidence=1.0,
                        page=body.get("page") or item.get("page"))
            if ex.issues:      # e.g. unknown unit - keep it in the queue
                item.update(status="review", issues=ex.issues)
            else:
                item.update(status="accepted", issues=[f"edited by reviewer {stamp}"])
        item["reviewed"] = True
        save_run(run)
    return jsonify(item)


def _results(run: dict) -> list[dict]:
    return [{"company": d["company"], "year": d["year"], "source": d["file"],
             "extractions": d.get("extractions", [])} for d in run["docs"] if d.get("extractions")]


@app.get("/api/runs/<run_id>/export.xlsx")
def export_xlsx(run_id):
    run = load_run(run_id)
    path = export(_results(run), FIELDS, run_dir(run_id) / f"esg_{run_id}.xlsx")
    return send_file(path, as_attachment=True, download_name=f"esg_data_{run_id}.xlsx")


@app.get("/api/runs/<run_id>/export.json")
def export_json(run_id):
    run = load_run(run_id)
    p = run_dir(run_id) / f"esg_{run_id}.json"
    p.write_text(json.dumps(_results(run), indent=2, default=str))
    return send_file(p, as_attachment=True, download_name=f"esg_data_{run_id}.json")


@app.get("/api/runs/<run_id>/pdf/<int:doc_i>")
def pdf(run_id, doc_i):
    run = load_run(run_id)
    if not 0 <= doc_i < len(run["docs"]):
        abort(404)
    return send_file(run_dir(run_id) / run["docs"][doc_i]["file"], mimetype="application/pdf")


if __name__ == "__main__":
    RUNS_DIR.mkdir(exist_ok=True)
    port = int(os.environ.get("PORT", 8000))
    from esg_pipeline import ocr
    eng = ocr.engine()
    print(f"ESG Extractor dashboard on http://localhost:{port}  "
          f"(Claude: {'on' if llm_available() else 'off'}, "
          f"OCR: {('on - ' + eng) if eng else 'off - run: pip install rapidocr_onnxruntime'})")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
