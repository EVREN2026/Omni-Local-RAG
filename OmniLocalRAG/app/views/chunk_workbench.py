"""ChunkWorkbench: import markdown and start chunking with user-configurable params."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from app.models.metadata_exporter import MetadataExporter
from app.models.stable_ids import stable_chunk_id
from app.utils import config as cfg
from app.utils.logger import logger
from app.models.chunker import coerce_chunk_item, markdown_to_items as _markdown_to_items
from app.utils.ui_loader import load_ui, require_child


class ChunkWorkbench(QWidget):
    """Chunk management page with explicit start action."""

    chunks_available = pyqtSignal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_file: Optional[str] = None
        self._current_source_type: str = "pdf"
        self._build()
        self._load_params()

    def _build(self) -> None:
        load_ui(self, "chunk_workbench.ui")

        self._file_label = require_child(self, QLabel, "fileLabel", "ChunkWorkbench UI")
        self._import_md_btn = require_child(self, QPushButton, "importMarkdownButton", "ChunkWorkbench UI")
        self._start_chunk_btn = require_child(self, QPushButton, "startChunkButton", "ChunkWorkbench UI")
        self._min_section_chars = require_child(self, QSpinBox, "minSectionCharsSpin", "ChunkWorkbench UI")
        self._keep_heading_cb = require_child(self, QCheckBox, "keepHeadingCheckBox", "ChunkWorkbench UI")
        self._status = require_child(self, QLabel, "statusLabel", "ChunkWorkbench UI")
        self._chunk_table = require_child(self, QTableWidget, "chunkTable", "ChunkWorkbench UI")

        # Insert slider+spinbox rows into host widgets from .ui
        max_chars_host = require_child(self, QWidget, "maxCharsHostWidget", "ChunkWorkbench UI")
        overlap_chars_host = require_child(self, QWidget, "overlapCharsHostWidget", "ChunkWorkbench UI")

        self._max_chars_spin = _insert_slider_spin_row(max_chars_host, 400, 4000, 100)
        self._overlap_chars_spin = _insert_slider_spin_row(overlap_chars_host, 0, 800, 50)

        # Apply dynamic properties
        self._file_label.setStyleSheet("color: #aaa; font-size: 11px;")
        self._min_section_chars.setRange(20, 500)
        self._min_section_chars.setSingleStep(10)

        self._chunk_table.setColumnCount(5)
        self._chunk_table.setHorizontalHeaderLabels(["#", "页", "Heading path", "Block", "内容预览"])
        self._chunk_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self._chunk_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._chunk_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)

        # Connect signals
        self._import_md_btn.clicked.connect(self._import_markdown_file)
        self._start_chunk_btn.clicked.connect(self._on_start_chunking)

    def load_file(self, file_path: str, source_type: str = "pdf") -> None:
        self._current_file = file_path
        st = (source_type or "pdf").strip().lower()
        self._current_source_type = st if st in {"pdf", "markdown"} else "pdf"
        self._file_label.setText(Path(file_path).name)
        self._load_existing_chunks()

    def _import_markdown_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Markdown 文件",
            "",
            "Markdown Files (*.md *.markdown);;All Files (*)",
        )
        if not path:
            return
        self.load_file(path, source_type="markdown")
        self._status.setText("已导入 Markdown，请点击“启动切块”")

    def _load_params(self) -> None:
        self._max_chars_spin.setValue(int(cfg.get("chunking.max_chars", 1800)))
        self._overlap_chars_spin.setValue(int(cfg.get("chunking.overlap_chars", 240)))
        self._min_section_chars.setValue(int(cfg.get("chunking.min_section_chars", 80)))
        self._keep_heading_cb.setChecked(bool(cfg.get("chunking.keep_heading_path", True)))

    def _save_params(self) -> None:
        config_path = cfg._CONFIG_PATH
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        except Exception:
            raw = {}
        raw.setdefault("chunking", {})
        raw["chunking"]["max_chars"] = self._max_chars_spin.value()
        raw["chunking"]["overlap_chars"] = self._overlap_chars_spin.value()
        raw["chunking"]["min_section_chars"] = self._min_section_chars.value()
        raw["chunking"]["keep_heading_path"] = self._keep_heading_cb.isChecked()
        config_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        cfg._cache.clear()

    def _resolve_markdown_path(self) -> Optional[Path]:
        if not self._current_file:
            return None
        if self._current_source_type == "markdown":
            return Path(self._current_file)
        return self._converted_md_path(self._current_file)

    def _on_start_chunking(self) -> None:
        self._run_chunking(show_message=True)

    def _run_chunking(self, show_message: bool = True) -> bool:
        if not self._current_file:
            if show_message:
                QMessageBox.warning(self, "提示", "请先导入文件或 Markdown。")
            self._status.setText("未选择来源文件")
            return False

        md_path = self._resolve_markdown_path()
        if md_path is None or not md_path.exists():
            if show_message:
                QMessageBox.warning(self, "提示", f"未找到 Markdown:\n{md_path}")
            self._status.setText("未找到可分块的 Markdown")
            return False

        self._save_params()
        markdown = md_path.read_text(encoding="utf-8", errors="replace")
        items = _markdown_to_items(markdown)
        rows = []
        used_ids = set()
        for item in items:
            chunk_item = coerce_chunk_item(item)
            if not str(chunk_item.display_content).strip():
                continue
            chunk_id = stable_chunk_id(
                source_file=self._current_file,
                source_type=self._current_source_type,
                page=chunk_item.page,
                heading_path=chunk_item.heading_path,
                content=chunk_item.display_content,
                used_ids=used_ids,
            )
            rows.append(
                {
                    "chunk_id": chunk_id,
                    "anchor_id": chunk_id,
                    "source_type": self._current_source_type,
                    "page": chunk_item.page,
                    "coords": chunk_item.coords,
                    "heading_path": chunk_item.heading_path,
                    "block_type": chunk_item.block_type,
                    "content": chunk_item.display_content,
                    "display_content": chunk_item.display_content,
                    "embedding_text": chunk_item.embedding_text,
                    "metadata": dict(chunk_item.metadata or {}),
                    "vector_dim": None,
                    "stored": False,
                    "is_manual": True,
                }
            )

        if not rows:
            if show_message:
                QMessageBox.warning(self, "提示", "Markdown 未生成任何切块。")
            self._status.setText("切块结果为空")
            return False

        MetadataExporter().export_chunks(
            source_file=self._current_file,
            chunks=rows,
            source_type=self._current_source_type,
        )
        self._populate_chunk_table(rows)
        self.chunks_available.emit(self._chunks_with_source_file(rows, self._current_file))
        self._status.setText(f"切块完成：{len(rows)} 条")
        logger.info(f"ChunkWorkbench: generated {len(rows)} chunks from {md_path}")
        return True

    def _load_existing_chunks(self) -> None:
        if not self._current_file:
            self._chunk_table.setRowCount(0)
            self._status.setText("尚未导入文件")
            return
        json_path = self._chunks_json_path(self._current_file, self._current_source_type)
        if not json_path.exists():
            self._chunk_table.setRowCount(0)
            self._status.setText("暂无已导出的切块，请点击“启动切块”")
            return
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            rows = payload.get("chunks", [])
            self._populate_chunk_table(rows)
            self.chunks_available.emit(self._chunks_with_source_file(rows, payload.get("source_file", self._current_file)))
            self._status.setText(f"已加载 {len(rows)} 条切块")
        except Exception as e:
            logger.error(f"ChunkWorkbench: load chunks failed: {e}", exc_info=True)
            self._status.setText("切块列表加载失败")

    def _populate_chunk_table(self, chunks: list) -> None:
        self._chunk_table.setRowCount(0)
        for i, chunk in enumerate(chunks):
            self._chunk_table.insertRow(i)
            preview = str(chunk.get("display_content") or chunk.get("content", ""))[:100].replace("\n", " ")
            values = [
                str(i + 1),
                str(chunk.get("page", "")),
                str(chunk.get("heading_path", "")),
                str(chunk.get("block_type", "text")),
                preview,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col in (0, 1):
                    item.setTextAlignment(Qt.AlignCenter)
                self._chunk_table.setItem(i, col, item)

    @staticmethod
    def _chunks_with_source_file(chunks: list, source_file: Optional[str]) -> list:
        source_name = Path(source_file).name if source_file else ""
        rows = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            row = dict(chunk)
            row.setdefault("source_file", source_name)
            rows.append(row)
        return rows

    def _converted_md_path(self, source_file: str) -> Path:
        root = Path(cfg.get("exports.path", "data/exports"))
        root = root if root.is_absolute() else cfg.abs_path(str(root))
        return root / "pdf" / f"{self._safe_export_stem(source_file)}.converted.md"

    def _chunks_json_path(self, source_file: str, source_type: str) -> Path:
        root = Path(cfg.get("exports.path", "data/exports"))
        root = root if root.is_absolute() else cfg.abs_path(str(root))
        return root / source_type / f"{self._safe_export_stem(source_file)}.chunks.json"

    @staticmethod
    def _safe_export_stem(source_file: str) -> str:
        stem = Path(source_file).stem or "document"
        return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "document"


def _insert_slider_spin_row(host: QWidget, min_val: int, max_val: int, step: int) -> QSpinBox:
    """Insert a slider+spinbox row into the host widget's existing layout."""
    layout = host.layout()
    if layout is None:
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)

    slider = QSlider(Qt.Horizontal)
    slider.setRange(min_val, max_val)
    slider.setSingleStep(step)
    slider.setPageStep(step * 5)
    slider.setFixedWidth(220)

    spin = QSpinBox()
    spin.setRange(min_val, max_val)
    spin.setSingleStep(step)
    spin.setFixedWidth(80)

    slider.valueChanged.connect(spin.setValue)
    spin.valueChanged.connect(slider.setValue)
    layout.addWidget(slider)
    layout.addWidget(spin)
    return spin
