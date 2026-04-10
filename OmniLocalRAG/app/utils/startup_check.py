from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from app.utils import config as cfg


@dataclass
class CheckItem:
    label: str
    path: str
    auto_create: bool = False
    download_url: str = ""
    # Extra required child paths that must exist inside `path` (for model dirs)
    required_children: List[str] = field(default_factory=list)
    ok: bool = field(default=False, init=False)
    message: str = field(default="", init=False)

    def run(self) -> bool:
        p = cfg.abs_path(self.path)
        if self.auto_create:
            p.mkdir(parents=True, exist_ok=True) if not p.suffix else p.touch()
        exists = p.exists()
        if exists and self.required_children:
            missing_children = [c for c in self.required_children if not (p / c).exists()]
            if missing_children:
                self.ok = False
                self.message = f"模型目录不完整，缺少: {', '.join(missing_children)}"
                return False
        self.ok = exists
        self.message = "OK" if self.ok else f"缺失: {p}"
        return self.ok


CHECKS: List[CheckItem] = [
    CheckItem(
        label="GGUF 模型文件",
        path=cfg.get("llm.model_path", "models/gemma-2-2b-it-q4_k_m.gguf"),
        download_url="https://huggingface.co/bartowski/gemma-2-2b-it-GGUF",
    ),
    CheckItem(
        label="BGE-M3 Embedding 模型",
        path=cfg.get("embed.model_path", "models/bge-m3"),
        download_url="https://huggingface.co/BAAI/bge-m3",
        # These files must exist inside the model directory.
        # If any is absent, sentence-transformers would fall back to a network
        # download, which we want to prevent entirely.
        required_children=[
            "pytorch_model.bin",
            "tokenizer.json",
            "config.json",
            "modules.json",
        ],
    ),
    CheckItem(
        label="VectorStore 数据目录",
        path="data/vectors",
        auto_create=True,
    ),
    CheckItem(
        label="SQLite 数据库",
        path="data/omni.db",
        auto_create=True,
    ),
    CheckItem(
        label="元数据导出目录",
        path=cfg.get("exports.path", "data/exports"),
        auto_create=True,
    ),
    CheckItem(
        label="问答评测记忆目录",
        path="data/eval",
        auto_create=True,
    ),
    CheckItem(
        label="缩略图缓存目录",
        path="data/thumbs",
        auto_create=True,
    ),
    CheckItem(
        label="API 任务模板目录",
        path="data/templates",
        auto_create=True,
    ),
]


def run_all() -> List[CheckItem]:
    for item in CHECKS:
        item.run()
    return CHECKS


def all_passed() -> bool:
    return all(c.ok for c in run_all())


def missing_items() -> List[CheckItem]:
    return [c for c in CHECKS if not c.ok]
