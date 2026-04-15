#!/usr/bin/env python3
"""
Direct parser smoke test (marker).
"""

from __future__ import annotations

import argparse
import sys
import time
import os  # 新增
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.parsers.document_parsers import PARSER_REGISTRY

PARSER_NAME = "marker"

# ... (保留原有的 _dedupe_paths 和 _collect_inputs 函数) ...

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
    
    # --- 新增参数 ---
    ap.add_argument(
        "--device", 
        choices=["cpu", "cuda"], 
        default="cpu", 
        help="Device to use for inference (default: cpu)"
    )
    # ----------------
    
    args = ap.parse_args()

    inputs = _collect_inputs(args.paths, args.glob)
    if not inputs:
        raise SystemExit("No input files found.")

    # 获取解析器实例
    parser = PARSER_REGISTRY[PARSER_NAME]
    
    # --- 关键修改：安全注入 device 选项 ---
    if hasattr(parser, "set_option"):
        parser.set_option("device", args.device)
    elif hasattr(parser, "options") and isinstance(parser.options, dict):
        parser.options["device"] = args.device
    else:
        # 如果以上都不行，可能是因为 options 在内部配置对象中
        # 强制注入（虽然有点暴力，但在测试脚本中很有效）
        print(f"[#] 警告: 无法直接访问 .options，尝试强制注入环境变量")
        os.environ["TORCH_DEVICE"] = args.device
    
    print(f"Using device: {args.device.upper()}")
    
    # 打印当前使用的设备，确认生效
    print(f"Using device: {args.device.upper()}")
    # --------------------------------

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
            import traceback # 增加报错详情方便排查
            traceback.print_exc()
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

    # ... (保留原有的 Summary 打印逻辑) ...
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