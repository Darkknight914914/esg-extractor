"""Stage 1 - Collect: download report PDFs listed in a sources CSV.

sources.csv columns: company, year, url
Writes the PDFs to <out_dir>/ and a manifest.csv the pipeline can run on.
Already-downloaded files are skipped, so the step can be re-run safely.
"""
from __future__ import annotations

import csv
import re
import time
import urllib.request
from pathlib import Path

USER_AGENT = "esg-extractor/0.1 (research prototype)"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def collect(sources_csv: str | Path, out_dir: str | Path, delay: float = 1.0) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "manifest.csv"
    with open(sources_csv, newline="", encoding="utf-8") as f, \
         open(manifest, "w", newline="", encoding="utf-8") as m:
        writer = csv.DictWriter(m, fieldnames=["company", "year", "pdf"])
        writer.writeheader()
        for row in csv.DictReader(f):
            name = f"{_slug(row['company'])}_{row['year']}.pdf"
            target = out_dir / name
            if not target.exists():
                req = urllib.request.Request(row["url"], headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                if not data.startswith(b"%PDF"):
                    print(f"skip {row['company']}: URL did not return a PDF")
                    continue
                target.write_bytes(data)
                print(f"downloaded {name} ({len(data) / 1e6:.1f} MB)")
                time.sleep(delay)          # be polite to company websites
            writer.writerow({"company": row["company"], "year": row["year"], "pdf": name})
    return manifest
