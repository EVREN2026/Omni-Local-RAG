#!/usr/bin/env python3
"""
Manual parser benchmark script.

Usage:
  PYTHONPATH=. python3 scripts/test_document_parsers.py --file /path/to/doc.pdf
  PYTHONPATH=. python3 scripts/test_document_parsers.py --dir /path/to/docs --glob "*.pdf"
  PYTHONPATH=. python3 scripts/test_document_parsers.py --file /path/to/doc.pdf --parsers docling marker
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.parsers.document_parsers import PARSER_REGISTRY


def _run_one(parser_name: str, file_path: str) -> dict:
    parser = PARSER_REGISTRY[parser_name]
    t0 = time.perf_counter()
    ok = True
    err = ""
    blocks = []
    try:
        blocks = parser.parse(file_path)
    except Exception as e:
        ok = False
        err = str(e)
    elapsed = time.perf_counter() - t0
    return {
        "parser": parser_name,
        "ok": ok,
        "seconds": round(elapsed, 3),
        "block_count": len(blocks),
        "error": err,
    }


def _collect_inputs(single_file: str, root_dir: str, pattern: str) -> list[Path]:
    if single_file:
        p = Path(single_file)
        return [p] if p.exists() else []
    if root_dir:
        d = Path(root_dir)
        if not d.exists():
            return []
        return sorted(d.rglob(pattern))
    return []


def main(default_parsers: list[str] | None = None) -> int:
    default_parsers = default_parsers or ["docling", "unstructured", "mineru", "marker", "ocr"]
    ap = argparse.ArgumentParser(description="Manual performance/success benchmark for document parsers")
    ap.add_argument("--file", default="", help="Input document path")
    ap.add_argument("--dir", default="", help="Directory for batch benchmark")
    ap.add_argument("--glob", default="*.pdf", help="Glob under --dir, e.g. '*.pdf' or '*.*'")
    ap.add_argument(
        "--parsers",
        nargs="*",
        default=default_parsers,
        help="Parsers to test",
    )
    ap.add_argument("--json", dest="json_out", default="", help="Optional output JSON path")
    args = ap.parse_args()

    inputs = _collect_inputs(args.file, args.dir, args.glob)
    if not inputs:
        raise SystemExit("No input files found. Use --file or --dir + --glob.")

    selected = []
    for name in args.parsers:
        if name not in PARSER_REGISTRY:
            print(f"[WARN] skip unknown parser: {name}")
            continue
        selected.append(name)

    if not selected:
        raise SystemExit("No valid parsers selected")

    rows = []
    print(f"Testing {len(inputs)} file(s)")
    for source in inputs:
        print(f"\nFile: {source}")
        for name in selected:
            row = _run_one(name, str(source))
            row["file"] = str(source)
            rows.append(row)
            status = "OK" if row["ok"] else "FAIL"
            print(
                f"[{status}] {name:12s}  "
                f"time={row['seconds']:>7.3f}s  blocks={row['block_count']:>5d}  "
                f"{row['error'][:120]}"
            )

    print("\n=== Summary (success rate / avg time) ===")
    for name in selected:
        items = [r for r in rows if r["parser"] == name]
        ok_count = sum(1 for r in items if r["ok"])
        total = len(items)
        avg_time = sum(r["seconds"] for r in items) / total if total else 0.0
        avg_blocks = sum(r["block_count"] for r in items) / total if total else 0.0
        success_rate = (ok_count / total * 100.0) if total else 0.0
        print(
            f"{name:12s}  success={ok_count}/{total} ({success_rate:5.1f}%)  "
            f"avg_time={avg_time:7.3f}s  avg_blocks={avg_blocks:7.1f}"
        )

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved report: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
