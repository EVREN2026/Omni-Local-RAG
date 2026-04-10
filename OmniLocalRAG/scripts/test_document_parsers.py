#!/usr/bin/env python3
"""
Manual parser benchmark script.

Usage:
  python scripts/test_document_parsers.py --file /path/to/doc.pdf
  python scripts/test_document_parsers.py /path/to/doc.pdf
  python scripts/test_document_parsers.py --dir /path/to/docs --glob "*.pdf"
  python scripts/test_document_parsers.py --file /path/to/doc.pdf --parsers docling marker
  python scripts/test_document_parsers.py --dir data/SOP --parsers docling marker --warmup 1 --repeat 3
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.parsers.document_parsers import PARSER_REGISTRY


def _run_one(parser_name: str, file_path: str, run_index: int, is_warmup: bool) -> dict:
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
        "run_index": run_index,
        "is_warmup": is_warmup,
        "ok": ok,
        "seconds": round(elapsed, 3),
        "block_count": len(blocks),
        "error": err,
    }


def _collect_inputs(single_file: str, root_dir: str, pattern: str, positional: list[str]) -> list[Path]:
    inputs: list[Path] = []
    if single_file:
        p = Path(single_file)
        if p.exists():
            inputs.append(p)
    if root_dir:
        d = Path(root_dir)
        if d.exists():
            inputs.extend(sorted(d.rglob(pattern)))
    for raw in positional:
        p = Path(raw)
        if p.is_file():
            inputs.append(p)
        elif p.is_dir():
            inputs.extend(sorted(p.rglob(pattern)))
    return _dedupe_paths(inputs)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen = set()
    result = []
    for path in paths:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _pick_file_dialog() -> list[Path]:
    """Fallback for running the script directly from an editor on Windows."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        selected = filedialog.askopenfilenames(
            title="选择要测试的文件",
            filetypes=[
                ("Documents", "*.pdf *.doc *.docx *.ppt *.pptx *.xls *.xlsx *.md *.txt"),
                ("All files", "*.*"),
            ],
        )
        root.destroy()
        return [Path(p) for p in selected if p]
    except Exception:
        return []


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    weight = rank - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


def _aggregate(rows: list[dict], selected: list[str]) -> dict:
    summary: dict[str, dict] = {}
    for name in selected:
        items = [r for r in rows if r["parser"] == name and not r.get("is_warmup", False)]
        seconds = sorted(float(r["seconds"]) for r in items)
        blocks = [int(r["block_count"]) for r in items]
        ok_count = sum(1 for r in items if r["ok"])
        total = len(items)
        summary[name] = {
            "total": total,
            "ok_count": ok_count,
            "success_rate": round((ok_count / total * 100.0), 3) if total else 0.0,
            "avg_time": round(statistics.mean(seconds), 3) if seconds else 0.0,
            "p50_time": round(_percentile(seconds, 0.50), 3) if seconds else 0.0,
            "p95_time": round(_percentile(seconds, 0.95), 3) if seconds else 0.0,
            "min_time": round(min(seconds), 3) if seconds else 0.0,
            "max_time": round(max(seconds), 3) if seconds else 0.0,
            "avg_blocks": round(statistics.mean(blocks), 3) if blocks else 0.0,
        }
    return summary


def _ensure_report_dir(path: str) -> Path:
    root = Path(path).expanduser()
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def _latest_previous_report(report_dir: Path, current_stem: str) -> Path | None:
    candidates = sorted(
        [
            p
            for p in report_dir.glob("*.report.json")
            if p.stem != current_stem and p.is_file()
        ]
    )
    return candidates[-1] if candidates else None


def _format_delta(cur: float, prev: float, unit: str = "") -> str:
    delta = cur - prev
    sign = "+" if delta >= 0 else ""
    return f"{cur:.3f}{unit} ({sign}{delta:.3f}{unit} vs prev)"


def _build_markdown_report(
    run_id: str,
    payload: dict,
    previous: dict | None,
) -> str:
    lines: list[str] = []
    lines.append(f"# Parser Benchmark Report ({run_id})")
    lines.append("")
    meta = payload["meta"]
    lines.append(f"- Timestamp: `{meta['timestamp']}`")
    lines.append(f"- Files: `{meta['file_count']}`")
    lines.append(f"- Warmup: `{meta['warmup']}`")
    lines.append(f"- Repeat: `{meta['repeat']}`")
    lines.append(f"- Parsers: `{', '.join(meta['parsers'])}`")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| parser | success | avg_time(s) | p50(s) | p95(s) | avg_blocks |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for parser in meta["parsers"]:
        s = payload["summary"][parser]
        lines.append(
            f"| {parser} | {s['ok_count']}/{s['total']} ({s['success_rate']:.1f}%) | "
            f"{s['avg_time']:.3f} | {s['p50_time']:.3f} | {s['p95_time']:.3f} | {s['avg_blocks']:.3f} |"
        )
    lines.append("")

    if previous:
        lines.append("## Delta Vs Previous")
        lines.append("")
        lines.append("| parser | success_rate | avg_time | p95_time | avg_blocks |")
        lines.append("|---|---:|---:|---:|---:|")
        prev_summary = previous.get("summary", {})
        for parser in meta["parsers"]:
            cur = payload["summary"].get(parser, {})
            prev = prev_summary.get(parser, {})
            if not prev:
                lines.append(
                    f"| {parser} | {cur.get('success_rate', 0.0):.3f}% (new) | "
                    f"{cur.get('avg_time', 0.0):.3f}s (new) | "
                    f"{cur.get('p95_time', 0.0):.3f}s (new) | "
                    f"{cur.get('avg_blocks', 0.0):.3f} (new) |"
                )
                continue
            lines.append(
                f"| {parser} | {_format_delta(cur['success_rate'], prev.get('success_rate', 0.0), '%')} | "
                f"{_format_delta(cur['avg_time'], prev.get('avg_time', 0.0), 's')} | "
                f"{_format_delta(cur['p95_time'], prev.get('p95_time', 0.0), 's')} | "
                f"{_format_delta(cur['avg_blocks'], prev.get('avg_blocks', 0.0), '')} |"
            )
        lines.append("")

    return "\n".join(lines)


def _write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "file",
        "parser",
        "run_index",
        "is_warmup",
        "ok",
        "seconds",
        "block_count",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def main(default_parsers: list[str] | None = None) -> int:
    default_parsers = default_parsers or ["docling", "unstructured", "mineru", "marker", "ocr"]
    ap = argparse.ArgumentParser(description="Manual performance/success benchmark for document parsers")
    ap.add_argument("paths", nargs="*", help="Input file(s) or directorie(s); directories use --glob")
    ap.add_argument("--file", default="", help="Input document path")
    ap.add_argument("--dir", default="", help="Directory for batch benchmark")
    ap.add_argument("--glob", default="*.pdf", help="Glob under --dir, e.g. '*.pdf' or '*.*'")
    ap.add_argument(
        "--parsers",
        nargs="*",
        default=default_parsers,
        help="Parsers to test",
    )
    ap.add_argument("--warmup", type=int, default=0, help="Warmup rounds per file/parser (excluded from stats)")
    ap.add_argument("--repeat", type=int, default=1, help="Measured rounds per file/parser")
    ap.add_argument(
        "--report-dir",
        default="reports/parser_benchmarks",
        help="Directory to save benchmark artifacts (json/csv/md)",
    )
    ap.add_argument("--no-report", action="store_true", help="Do not write benchmark artifacts")
    ap.add_argument("--json", dest="json_out", default="", help="Optional output JSON path")
    args = ap.parse_args()

    if args.warmup < 0:
        raise SystemExit("--warmup must be >= 0")
    if args.repeat <= 0:
        raise SystemExit("--repeat must be >= 1")

    inputs = _collect_inputs(args.file, args.dir, args.glob, args.paths)
    if not inputs and not args.file and not args.dir and not args.paths:
        inputs = _pick_file_dialog()
    if not inputs:
        raise SystemExit(
            "No input files found. Use --file, pass a file path directly, or use --dir + --glob."
        )

    selected = []
    for name in args.parsers:
        if name not in PARSER_REGISTRY:
            print(f"[WARN] skip unknown parser: {name}")
            continue
        selected.append(name)

    if not selected:
        raise SystemExit("No valid parsers selected")

    rows = []
    run_count = args.warmup + args.repeat
    print(f"Testing {len(inputs)} file(s)")
    for source in inputs:
        print(f"\nFile: {source}")
        for run_index in range(1, run_count + 1):
            is_warmup = run_index <= args.warmup
            round_tag = f"warmup {run_index}" if is_warmup else f"run {run_index - args.warmup}"
            print(f"  Round: {round_tag}")
            for name in selected:
                row = _run_one(name, str(source), run_index, is_warmup)
                row["file"] = str(source)
                rows.append(row)
                status = "OK" if row["ok"] else "FAIL"
                label = f"{name} (warmup)" if is_warmup else name
                print(
                    f"[{status}] {label:12s}  "
                    f"time={row['seconds']:>7.3f}s  blocks={row['block_count']:>5d}  "
                    f"{row['error'][:120]}"
                )

    print("\n=== Summary (success rate / avg time) ===")
    summary = _aggregate(rows, selected)
    for name in selected:
        s = summary[name]
        print(
            f"{name:12s}  success={s['ok_count']}/{s['total']} ({s['success_rate']:5.1f}%)  "
            f"avg_time={s['avg_time']:7.3f}s  avg_blocks={s['avg_blocks']:7.1f}  "
            f"p50={s['p50_time']:7.3f}s  p95={s['p95_time']:7.3f}s"
        )

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"benchmark-{timestamp}"
    payload = {
        "meta": {
            "timestamp": timestamp,
            "file_count": len(inputs),
            "files": [str(p) for p in inputs],
            "parsers": selected,
            "warmup": args.warmup,
            "repeat": args.repeat,
            "script": str(Path(__file__).resolve()),
        },
        "summary": summary,
        "rows": rows,
    }

    if not args.no_report:
        report_dir = _ensure_report_dir(args.report_dir)
        report_json = report_dir / f"{run_id}.report.json"
        report_csv = report_dir / f"{run_id}.rows.csv"
        report_md = report_dir / f"{run_id}.report.md"
        prev_path = _latest_previous_report(report_dir, report_json.stem)
        previous = None
        if prev_path and prev_path.exists():
            try:
                previous = json.loads(prev_path.read_text(encoding="utf-8"))
            except Exception:
                previous = None
        report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_csv(report_csv, rows)
        md = _build_markdown_report(run_id, payload, previous)
        report_md.write_text(md, encoding="utf-8")
        print(f"\nSaved report JSON: {report_json}")
        print(f"Saved report CSV : {report_csv}")
        print(f"Saved report MD  : {report_md}")
        if prev_path:
            print(f"Compared previous: {prev_path}")

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved report: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
