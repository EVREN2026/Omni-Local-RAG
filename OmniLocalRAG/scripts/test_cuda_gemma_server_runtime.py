#!/usr/bin/env python
"""
Runtime CUDA verification for Gemma via llama-server.

The script starts a temporary llama-server instance on a probe port, sends one
completion request, then parses server logs for GPU offload markers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.utils import config as cfg

_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_get_json(url: str, timeout: int = 5) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    with _LOCAL_OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _http_post_json(url: str, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _LOCAL_OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _wait_health(base_url: str, timeout_sec: int) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            data = _http_get_json(f"{base_url}/health", timeout=3)
            if data.get("status") == "ok":
                return True
        except Exception:
            time.sleep(0.5)
    return False


def _parse_offload(log_text: str) -> tuple[int, int]:
    m = re.search(r"offloaded\s+(\d+)/(\d+)\s+layers\s+to\s+GPU", log_text, re.IGNORECASE)
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Gemma llama-server CUDA runtime")
    parser.add_argument("--port", type=int, default=8012, help="Temporary probe port")
    parser.add_argument("--timeout", type=int, default=180, help="Startup timeout seconds")
    args = parser.parse_args()

    server_dir = cfg.abs_path(cfg.get("llama_server.server_dir", ""))
    exe_name = "llama-server.exe" if os.name == "nt" else "llama-server"
    exe = server_dir / exe_name
    model_path = cfg.abs_path(cfg.get("llama_server.model_path", cfg.get("llm.model_path", "")))
    host = str(cfg.get("llama_server.host", "127.0.0.1"))
    n_gpu_layers = int(cfg.get("llama_server.n_gpu_layers", cfg.get("llm.n_gpu_layers", 999)))
    ctx_size = int(cfg.get("llama_server.ctx_size", cfg.get("llm.n_ctx", 2048)))
    flash_attn = bool(cfg.get("llama_server.flash_attn", True))
    no_mmap = bool(cfg.get("llama_server.no_mmap", True))
    numa = str(cfg.get("llama_server.numa", "distribute"))

    report: dict[str, Any] = {
        "exe": str(exe),
        "model_path": str(model_path),
        "host": host,
        "port": args.port,
        "n_gpu_layers": n_gpu_layers,
        "ctx_size": ctx_size,
    }

    if not exe.exists():
        report["error"] = f"llama-server executable not found: {exe}"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    if not model_path.exists():
        report["error"] = f"GGUF model not found: {model_path}"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    with tempfile.TemporaryDirectory() as td:
        out_log = Path(td) / "llama_server_probe.out.log"
        err_log = Path(td) / "llama_server_probe.err.log"
        cmd = [
            str(exe),
            "-m",
            str(model_path),
            "--host",
            host,
            "--port",
            str(args.port),
            "--ctx-size",
            str(ctx_size),
            "--n-gpu-layers",
            str(n_gpu_layers),
            "--numa",
            numa,
        ]
        if flash_attn:
            cmd += ["--flash-attn", "on"]
        if no_mmap:
            cmd += ["--no-mmap"]

        report["cmd"] = cmd
        with open(out_log, "w", encoding="utf-8") as out_fp, open(err_log, "w", encoding="utf-8") as err_fp:
            proc = subprocess.Popen(
                cmd,
                cwd=str(server_dir),
                stdout=out_fp,
                stderr=err_fp,
            )

            base_url = f"http://{host}:{args.port}"
            try:
                healthy = _wait_health(base_url, args.timeout)
                report["health_ok"] = healthy
                if healthy:
                    resp = _http_post_json(
                        f"{base_url}/completion",
                        {
                            "prompt": "Only output digits: 11+12=",
                            "n_predict": 8,
                            "temperature": 0,
                            "stream": False,
                        },
                        timeout=60,
                    )
                    report["completion_text"] = str(resp.get("content", "")).strip()
                else:
                    report["completion_text"] = ""
            finally:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()

        out_text = out_log.read_text(encoding="utf-8", errors="replace") if out_log.exists() else ""
        err_text = err_log.read_text(encoding="utf-8", errors="replace") if err_log.exists() else ""
        merged = out_text + "\n" + err_text

        offloaded, total = _parse_offload(merged)
        report["offloaded_layers"] = offloaded
        report["total_layers"] = total
        report["gpu_offload_detected"] = offloaded > 0
        report["log_tail"] = "\n".join((merged.splitlines()[-30:]))

    print(json.dumps(report, ensure_ascii=False, indent=2))

    ok = bool(report.get("health_ok")) and bool(report.get("completion_text")) and bool(report.get("gpu_offload_detected"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
