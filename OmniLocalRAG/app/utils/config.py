import json
from pathlib import Path
from typing import Any

_BASE = Path(__file__).parent.parent.parent
_CONFIG_PATH = _BASE / "config.json"

_DEFAULT: dict = {
    "llm": {
        "enabled": True,
        "model_path": "models/gemma-4-2b-it-q4_k_m.gguf",
        "n_gpu_layers": 999,
        "n_ctx": 8192,
        "max_tokens": 512,
        "temperature": 0.7,
        "repeat_penalty": 1.1,
        "top_k": 40,
        "top_p": 0.9,
        "min_p": 0.05,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
    },
    "llama_server": {
        # llama-server.exe 专用配置；留空则继承 llm.* 对应字段
        "model_path":       "",                          # 独立指定时覆盖 llm.model_path
        "server_dir":       "models/llama-b8747-bin-win-cuda-12.4-x64",
        "host":             "127.0.0.1",
        "port":             8000,
        "n_gpu_layers":     999,
        "ctx_size":         8192,
        "threads":          -1,                          # -1 = llama-server 自动
        "parallel":         2,
        "batch_size":       1024,
        "ubatch_size":      512,
        "flash_attn":       True,
        "no_mmap":          True,
        "numa":             "distribute",
        "extra_args":       "",
        "auto_download":    True,                        # 找不到时从 GitHub 下载
        "startup_timeout":  180,
    },
    "retrieval": {
        "top_k": 5,
        "distance_threshold": 0.7,
    },
    "router": {
        "enabled": True,
        "memory_window": 5,
        "few_shot_examples": True,
        "category_filter_enabled": True,
        "max_validate_retries": 3,
    },
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
        "min_section_chars": 80,   # minimum body length for a section to be its own chunk
    },
    "pdf": {
        "parser_order": ["marker"],
        "marker_device": "cpu",
        "index_on_import": False,
        "parser_options": {
            "marker": {"device": "cpu"},
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

