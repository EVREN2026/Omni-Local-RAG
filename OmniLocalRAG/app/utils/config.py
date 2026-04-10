import json
from pathlib import Path
from typing import Any

_BASE = Path(__file__).parent.parent.parent
_CONFIG_PATH = _BASE / "config.json"

_DEFAULT: dict = {
    "llm": {
        "enabled": True,
        "subprocess": True,
        "model_path": "models/gemma-2-2b-it-q4_k_m.gguf",
        "n_gpu_layers": 0,
        "n_ctx": 4096,
        "max_tokens": 512,
        "temperature": 0.7,
        "repeat_penalty": 1.1,
    },
    "embed": {"model_path": "models/bge-m3", "device": "cpu"},
    "retrieval": {"top_k": 5, "distance_threshold": 0.7},
    "qa_memory": {
        "enabled": True,
        "path": "data/eval/qa_dialogue_memory.md",
        "jsonl_path": "data/eval/qa_dialogue_memory.jsonl",
        "max_context_chars": 1200,
    },
    "chunking": {
        "max_chars": 1800,
        "overlap_chars": 240,
        "keep_heading_path": True,
    },
    "pdf": {
        "parser_order": ["docling", "unstructured", "mineru", "marker", "ocr"],
        "marker_device": "cpu",
        "mineru_cmd": "mineru",
        "index_on_import": False,
        "parser_options": {
            "docling": {},
            "unstructured": {"strategy": "auto", "languages": "eng,chi_sim"},
            "mineru": {"command": "mineru", "extra_args": ""},
            "marker": {"device": "cpu"},
            "ocr": {"lang": "chi_sim+eng", "scale": 2},
        },
    },
    "asr": {
        "subprocess": True,
        "model_size": "base",
        "device": "cpu",
        "compute_type": "int8",
        "language": "zh",
        "beam_size": 5,
        "vad_filter": True,
    },
    "ui": {
        "hotkey": "alt+space",
        "window_width": 680,
        "window_opacity": 0.95,
        "animation_ms": 80,
    },
    "idle_timeout_minutes": 10,
    "log_retention_days": 30,
    "exports": {
        "path": "data/exports",
        "write_json": True,
        "write_markdown": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


_cache: dict = {}


def load() -> dict:
    global _cache
    if _cache:
        return _cache
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                user = json.load(f)
            _cache = _deep_merge(_DEFAULT, user)
            return _cache
        except Exception:
            pass
    _cache = dict(_DEFAULT)
    return _cache


def get(key_path: str, default: Any = None) -> Any:
    """Dot-separated key access, e.g. get('llm.n_gpu_layers')."""
    cfg = load()
    keys = key_path.split(".")
    for k in keys:
        if isinstance(cfg, dict):
            cfg = cfg.get(k)
        else:
            return default
    return cfg if cfg is not None else default


def abs_path(relative: str) -> Path:
    """Resolve a config-relative path to an absolute path."""
    return _BASE / relative
