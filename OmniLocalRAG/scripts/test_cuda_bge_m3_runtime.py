#!/usr/bin/env python
"""
Runtime CUDA verification for BGE-M3 embedding model.

This script checks:
1) torch CUDA availability
2) EmbedManager load success
3) actual model device reported by SentenceTransformer
4) a real encode call and CUDA memory usage snapshot
"""

from __future__ import annotations

import json
import sys
from typing import Any
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import torch  # type: ignore

from app.models.embed_manager import EmbedManager
from app.utils import config as cfg


def _cuda_mem_stats() -> dict[str, Any]:
    if not torch.cuda.is_available():
        return {
            "available": False,
            "allocated": 0,
            "reserved": 0,
            "max_allocated": 0,
            "max_reserved": 0,
        }
    idx = 0
    return {
        "available": True,
        "allocated": int(torch.cuda.memory_allocated(idx)),
        "reserved": int(torch.cuda.memory_reserved(idx)),
        "max_allocated": int(torch.cuda.max_memory_allocated(idx)),
        "max_reserved": int(torch.cuda.max_memory_reserved(idx)),
    }


def main() -> int:
    report: dict[str, Any] = {
        "python_executable": sys.executable,
        "torch_version": torch.__version__,
        "torch_cuda_available": bool(torch.cuda.is_available()),
        "torch_cuda_version": torch.version.cuda,
        "embed_config_device": cfg.get("embed.device"),
        "embed_model_path": str(cfg.abs_path(cfg.get("embed.model_path"))),
    }

    if torch.cuda.is_available():
        report["gpu_name"] = torch.cuda.get_device_name(0)
        report["gpu_arch_list"] = list(torch.cuda.get_arch_list())

    manager = EmbedManager()
    before = _cuda_mem_stats()
    ok = manager.load()
    after_load = _cuda_mem_stats()

    report["load_ok"] = ok
    report["load_error"] = manager.last_error
    model = getattr(manager, "_model", None)
    report["model_device"] = str(getattr(model, "device", None))
    report["mem_before"] = before
    report["mem_after_load"] = after_load

    if ok:
        vectors = manager.encode(
            [
                "BGE-M3 CUDA runtime check sentence one.",
                "BGE-M3 CUDA runtime check sentence two.",
            ]
        )
        after_encode = _cuda_mem_stats()
        report["encode_ok"] = True
        report["vector_count"] = len(vectors)
        report["vector_dim"] = len(vectors[0]) if vectors else 0
        report["mem_after_encode"] = after_encode
    else:
        report["encode_ok"] = False

    print(json.dumps(report, ensure_ascii=False, indent=2))

    is_cuda_model = str(report.get("model_device", "")).startswith("cuda")
    if ok and report["torch_cuda_available"] and is_cuda_model:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
