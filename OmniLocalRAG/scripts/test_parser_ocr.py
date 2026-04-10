#!/usr/bin/env python3
"""
Direct parser smoke test (ocr).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.parsers.document_parsers import PARSER_REGISTRY


PARSER_NAME = "ocr"


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen = set()
    result = []
    for path in paths:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _collect_inputs(raw_paths: list[str], pattern: str) -> list[Path]:
    inputs: list[Path] = []
    for raw in raw_paths:
        p = Path(raw)
        if p.is_file():
            inputs.append(p)
        elif p.is_dir():
            inputs.extend(sorted(p.rglob(pattern)))
    return _dedupe_paths(inputs)


def main() -> int:
    ap = argparse.ArgumentParser(description=f"Smoke test for parser: {PARSER_NAME}")
    ap.add_argument("paths", nargs="+", help="Input file(s) or directorie(s)")
    ap.add_argument("--glob", default="*.pdf", help="Glob when a path is a directory")
    args = ap.parse_args()

    inputs = _collect_inputs(args.paths, args.glob)
    if not inputs:
        raise SystemExit("No input files found.")

    parser = PARSER_REGISTRY[PARSER_NAME]
    rows = []
    print(f"Testing {len(inputs)} file(s)")
    for source in inputs:
        print(f"\nFile: {source}")
        t0 = time.perf_counter()
        ok = True
        err = ""
        blocks = []
        try:
            blocks = parser.parse(str(source))
        except Exception as e:
            ok = False
            err = str(e)
        elapsed = time.perf_counter() - t0
        rows.append((ok, elapsed, len(blocks)))
        status = "OK" if ok else "FAIL"
        print(
            f"[{status}] {PARSER_NAME:12s}  "
            f"time={elapsed:>7.3f}s  blocks={len(blocks):>5d}  "
            f"{err[:120]}"
        )

    total = len(rows)
    ok_count = sum(1 for ok, _, _ in rows if ok)
    avg_time = sum(sec for _, sec, _ in rows) / total if total else 0.0
    avg_blocks = sum(cnt for _, _, cnt in rows) / total if total else 0.0
    success_rate = (ok_count / total * 100.0) if total else 0.0
    print("\n=== Summary (success rate / avg time) ===")
    print(
        f"{PARSER_NAME:12s}  success={ok_count}/{total} ({success_rate:5.1f}%)  "
        f"avg_time={avg_time:7.3f}s  avg_blocks={avg_blocks:7.1f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
