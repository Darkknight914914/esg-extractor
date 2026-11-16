"""Command line entry point.

  python -m esg_pipeline collect --sources sources.csv --out reports/
  python -m esg_pipeline run --manifest reports/manifest.csv --extractor hybrid --out output/esg.xlsx
  python -m esg_pipeline run --pdf report.pdf --company "Acme AG" --year 2025
"""
from __future__ import annotations

import argparse
import logging

from .export import export
from .pipeline import run_batch, run_one
from .schema import DEFAULT_SCHEMA, load_schema


def main() -> None:
    ap = argparse.ArgumentParser(prog="esg_pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="download report PDFs from a sources CSV")
    c.add_argument("--sources", required=True)
    c.add_argument("--out", default="reports")

    r = sub.add_parser("run", help="extract + validate + export")
    r.add_argument("--manifest", help="CSV with company,year,pdf")
    r.add_argument("--pdf")
    r.add_argument("--company")
    r.add_argument("--year", type=int)
    r.add_argument("--extractor", choices=["rules", "llm", "hybrid"], default="rules")
    r.add_argument("--model", help="override the Claude model id")
    r.add_argument("--schema", default=str(DEFAULT_SCHEMA))
    r.add_argument("--out", default="output/esg_data.xlsx")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.cmd == "collect":
        from .collect import collect
        print("manifest written to", collect(args.sources, args.out))
        return

    fields = load_schema(args.schema)
    llm = None
    if args.extractor in ("llm", "hybrid"):
        from .extract_llm import DEFAULT_MODEL, LLMExtractor
        llm = LLMExtractor(model=args.model or DEFAULT_MODEL)

    if args.manifest:
        results = run_batch(args.manifest, fields, args.extractor, llm)
    elif args.pdf and args.company and args.year:
        results = [run_one(args.company, args.year, args.pdf, fields, args.extractor, llm)]
    else:
        ap.error("give --manifest, or --pdf with --company and --year")

    path = export(results, fields, args.out)
    for res in results:
        st = [e["status"] for e in res["extractions"]]
        print(f"{res['company']} {res['year']}: {st.count('accepted')} accepted, "
              f"{st.count('review')} to review, {st.count('missing')} missing")
    print("written:", path, "and", path.with_suffix(".json"))


if __name__ == "__main__":
    main()
