"""Cross-modal binding management panel."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.sqlite_store import SQLiteStore
from app.utils import config as cfg
from app.utils.logger import logger


class CrossModalPanel(QWidget):
    """Manage document anchors, video clips, and saved cross-modal bindings."""

    binding_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._anchors: List[Dict] = []
        self._clips: List[Dict] = []
        self._bindings: List[Dict] = []
        self._anchor_map: Dict[str, Dict] = {}
        SQLiteStore().init()
        self._build()
        self.refresh_all()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        top = QHBoxLayout()
        title = QLabel("跨模态绑定管理")
        title.setStyleSheet("font-weight: bold;")
        self._status = QLabel("")
        self._status.setStyleSheet("color: #777;")
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_all)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self._status)
        top.addWidget(refresh_btn)
        root.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_anchor_box())
        splitter.addWidget(self._build_clip_box())
        splitter.addWidget(self._build_binding_box())
        splitter.setSizes([440, 360, 520])
        root.addWidget(splitter, 1)

    def _build_anchor_box(self) -> QWidget:
        box = QGroupBox("文档 Anchor")
        layout = QVBoxLayout(box)
        self._anchor_filter = QLineEdit()
        self._anchor_filter.setPlaceholderText("筛选文件、anchor_id、标题、内容")
        self._anchor_filter.textChanged.connect(self._populate_anchor_table)
        layout.addWidget(self._anchor_filter)

        self._anchor_table = QTableWidget()
        self._anchor_table.setColumnCount(5)
        self._anchor_table.setHorizontalHeaderLabels(["来源", "anchor_id", "页", "标题", "内容预览"])
        self._setup_table(self._anchor_table)
        self._anchor_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self._anchor_table, 1)
        return box

    def _build_clip_box(self) -> QWidget:
        box = QGroupBox("视频 Clip")
        layout = QVBoxLayout(box)
        self._clip_filter = QLineEdit()
        self._clip_filter.setPlaceholderText("筛选视频、摘要、clip_id")
        self._clip_filter.textChanged.connect(self._populate_clip_table)
        layout.addWidget(self._clip_filter)

        self._clip_table = QTableWidget()
        self._clip_table.setColumnCount(5)
        self._clip_table.setHorizontalHeaderLabels(["视频", "时间", "摘要", "clip_id", "向量"])
        self._setup_table(self._clip_table)
        self._clip_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self._clip_table, 1)
        return box

    def _build_binding_box(self) -> QWidget:
        box = QGroupBox("绑定记录")
        layout = QVBoxLayout(box)
        self._binding_filter = QLineEdit()
        self._binding_filter.setPlaceholderText("筛选视频、摘要、anchor、状态")
        self._binding_filter.textChanged.connect(self._populate_binding_table)
        layout.addWidget(self._binding_filter)

        self._binding_table = QTableWidget()
        self._binding_table.setColumnCount(7)
        self._binding_table.setHorizontalHeaderLabels(
            ["视频", "时间", "摘要", "Anchor来源", "Anchor", "状态", "绑定ID"]
        )
        self._setup_table(self._binding_table)
        self._binding_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self._binding_table, 1)

        actions = QHBoxLayout()
        bind_btn = QPushButton("绑定选中")
        bind_btn.clicked.connect(self._bind_selected)
        repair_btn = QPushButton("修复到选中 Anchor")
        repair_btn.clicked.connect(self._repair_selected)
        delete_btn = QPushButton("删除绑定")
        delete_btn.clicked.connect(self._delete_selected)
        actions.addWidget(bind_btn)
        actions.addWidget(repair_btn)
        actions.addWidget(delete_btn)
        actions.addStretch()
        layout.addLayout(actions)
        return box

    @staticmethod
    def _setup_table(table: QTableWidget) -> None:
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def refresh_all(self) -> None:
        self.refresh_anchors()
        self.refresh_clips()
        self.refresh_bindings()
        self._update_status()

    def refresh_anchors(self) -> None:
        self._anchors = _load_all_anchors()
        self._anchor_map = {row["anchor_id"]: row for row in self._anchors}
        self._populate_anchor_table()

    def refresh_clips(self) -> None:
        try:
            self._clips = SQLiteStore().list_clips()
        except Exception as e:
            logger.error(f"CrossModalPanel: list clips failed: {e}", exc_info=True)
            self._clips = []
        self._populate_clip_table()

    def refresh_bindings(self) -> None:
        try:
            self._bindings = SQLiteStore().list_cross_modal()
        except Exception as e:
            logger.error(f"CrossModalPanel: list bindings failed: {e}", exc_info=True)
            self._bindings = []
        self._populate_binding_table()
        self._update_status()

    def _populate_anchor_table(self) -> None:
        rows = [row for row in self._anchors if _matches_filter(row, self._anchor_filter.text())]
        self._anchor_table.setRowCount(0)
        for row_index, row in enumerate(rows):
            self._anchor_table.insertRow(row_index)
            values = [
                row.get("source_file", ""),
                row.get("anchor_id", ""),
                str(row.get("page", "")),
                row.get("heading_path", ""),
                _preview(row.get("content", ""), 90),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, row)
                self._anchor_table.setItem(row_index, col, item)

    def _populate_clip_table(self) -> None:
        rows = [row for row in self._clips if _matches_filter(row, self._clip_filter.text())]
        self._clip_table.setRowCount(0)
        for row_index, row in enumerate(rows):
            self._clip_table.insertRow(row_index)
            values = [
                row.get("video_file", ""),
                _time_range(row),
                row.get("semantic_summary", ""),
                row.get("id", ""),
                "OK" if row.get("chroma_id") else "未入向量",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, row)
                self._clip_table.setItem(row_index, col, item)

    def _populate_binding_table(self) -> None:
        enriched = [self._enrich_binding(row) for row in self._bindings]
        rows = [row for row in enriched if _matches_filter(row, self._binding_filter.text())]
        self._binding_table.setRowCount(0)
        for row_index, row in enumerate(rows):
            self._binding_table.insertRow(row_index)
            values = [
                row.get("video_file", ""),
                _time_range(row),
                row.get("semantic_summary", ""),
                row.get("anchor_source", ""),
                row.get("pdf_anchor_id", ""),
                row.get("status", ""),
                row.get("id", ""),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, row)
                self._binding_table.setItem(row_index, col, item)

    def _enrich_binding(self, binding: Dict) -> Dict:
        row = dict(binding)
        anchor = self._anchor_map.get(str(binding.get("pdf_anchor_id", "")))
        if anchor:
            row["anchor_source"] = anchor.get("source_file", "")
            row["anchor_preview"] = anchor.get("content", "")
            row["status"] = "OK"
        else:
            row["anchor_source"] = ""
            row["anchor_preview"] = ""
            row["status"] = "Anchor缺失"
        if not binding.get("video_clip_id") or not binding.get("video_file"):
            row["status"] = "Clip缺失" if row["status"] == "OK" else f"{row['status']} / Clip缺失"
        return row

    def _bind_selected(self) -> None:
        anchor = self._selected_row_data(self._anchor_table)
        clip = self._selected_row_data(self._clip_table)
        if not anchor or not clip:
            QMessageBox.information(self, "提示", "请先分别选择一个文档 Anchor 和一个视频 Clip")
            return
        map_id = SQLiteStore().insert_cross_modal(
            video_clip_id=str(clip["id"]),
            pdf_anchor_id=str(anchor["anchor_id"]),
        )
        self.refresh_bindings()
        self.binding_changed.emit()
        self._status.setText(f"已绑定: {map_id[:8]}...")

    def _repair_selected(self) -> None:
        binding = self._selected_row_data(self._binding_table)
        anchor = self._selected_row_data(self._anchor_table)
        if not binding or not anchor:
            QMessageBox.information(self, "提示", "请先选择一条绑定记录和一个新的 Anchor")
            return
        SQLiteStore().update_cross_modal_anchor(
            map_id=str(binding["id"]),
            pdf_anchor_id=str(anchor["anchor_id"]),
        )
        self.refresh_bindings()
        self.binding_changed.emit()
        self._status.setText(f"已修复绑定: {str(binding['id'])[:8]}...")

    def _delete_selected(self) -> None:
        binding = self._selected_row_data(self._binding_table)
        if not binding:
            QMessageBox.information(self, "提示", "请先选择一条绑定记录")
            return
        if QMessageBox.question(self, "确认删除", "确认删除这条跨模态绑定吗？") != QMessageBox.Yes:
            return
        SQLiteStore().delete_cross_modal(str(binding["id"]))
        self.refresh_bindings()
        self.binding_changed.emit()
        self._status.setText("绑定已删除")

    @staticmethod
    def _selected_row_data(table: QTableWidget) -> Optional[Dict]:
        row = table.currentRow()
        if row < 0:
            return None
        item = table.item(row, 0)
        data = item.data(Qt.UserRole) if item else None
        return data if isinstance(data, dict) else None

    def _update_status(self) -> None:
        self._status.setText(
            f"Anchors: {len(self._anchors)} | Clips: {len(self._clips)} | Bindings: {len(self._bindings)}"
        )


def _load_all_anchors() -> List[Dict]:
    exports_root = cfg.abs_path(cfg.get("exports.path", "data/exports"))
    anchors: List[Dict] = []
    if not exports_root.exists():
        return anchors

    for json_path in sorted(exports_root.rglob("*.chunks.json")):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"CrossModalPanel: skip invalid chunks json {json_path}: {e}")
            continue

        source_file = payload.get("source_file") or json_path.name
        source_type = payload.get("source_type") or json_path.parent.name
        for index, chunk in enumerate(payload.get("chunks", []), start=1):
            if not isinstance(chunk, dict):
                continue
            anchor_id = str(chunk.get("anchor_id") or chunk.get("chunk_id") or "").strip()
            if not anchor_id:
                continue
            anchors.append(
                {
                    "source_file": str(source_file),
                    "source_type": str(source_type),
                    "json_path": str(json_path),
                    "index": index,
                    "anchor_id": anchor_id,
                    "page": chunk.get("page", ""),
                    "heading_path": str(chunk.get("heading_path", "")),
                    "content": str(chunk.get("content", "")),
                }
            )
    return anchors


def _matches_filter(row: Dict, text: str) -> bool:
    needle = (text or "").strip().lower()
    if not needle:
        return True
    haystack = " ".join(str(value) for value in row.values()).lower()
    return needle in haystack


def _preview(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _time_range(row: Dict) -> str:
    start = row.get("start_sec", row.get("start", 0)) or 0
    end = row.get("end_sec", row.get("end", 0)) or 0
    try:
        return f"{float(start):.1f}s-{float(end):.1f}s"
    except (TypeError, ValueError):
        return f"{start}s-{end}s"
