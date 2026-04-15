"""Cross-modal binding management panel."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from app.models.sqlite_store import SQLiteStore
from app.utils import config as cfg
from app.utils.logger import logger
from app.utils.ui_loader import load_ui, require_child


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
        load_ui(self, "cross_modal_panel.ui")

        self._title = require_child(self, QLabel, "titleLabel", "CrossModalPanel UI")
        self._status = require_child(self, QLabel, "statusLabel", "CrossModalPanel UI")
        self._refresh_btn = require_child(self, QPushButton, "refreshButton", "CrossModalPanel UI")
        self._splitter = require_child(self, QSplitter, "mainSplitter", "CrossModalPanel UI")

        self._anchor_filter = require_child(self, QLineEdit, "anchorFilterEdit", "CrossModalPanel UI")
        self._anchor_table = require_child(self, QTableWidget, "anchorTable", "CrossModalPanel UI")
        self._clip_filter = require_child(self, QLineEdit, "clipFilterEdit", "CrossModalPanel UI")
        self._clip_table = require_child(self, QTableWidget, "clipTable", "CrossModalPanel UI")
        self._binding_filter = require_child(self, QLineEdit, "bindingFilterEdit", "CrossModalPanel UI")
        self._binding_table = require_child(self, QTableWidget, "bindingTable", "CrossModalPanel UI")
        self._bind_btn = require_child(self, QPushButton, "bindSelectedButton", "CrossModalPanel UI")
        self._repair_btn = require_child(self, QPushButton, "repairSelectedButton", "CrossModalPanel UI")
        self._delete_btn = require_child(self, QPushButton, "deleteBindingButton", "CrossModalPanel UI")

        self._title.setStyleSheet("font-weight: bold;")
        self._status.setStyleSheet("color: #777;")
        self._refresh_btn.clicked.connect(self.refresh_all)
        self._anchor_filter.textChanged.connect(self._populate_anchor_table)
        self._clip_filter.textChanged.connect(self._populate_clip_table)
        self._binding_filter.textChanged.connect(self._populate_binding_table)
        self._bind_btn.clicked.connect(self._bind_selected)
        self._repair_btn.clicked.connect(self._repair_selected)
        self._delete_btn.clicked.connect(self._delete_selected)

        self._anchor_table.setColumnCount(5)
        self._anchor_table.setHorizontalHeaderLabels(["来源", "anchor_id", "页码", "标题", "内容预览"])
        self._setup_table(self._anchor_table)
        self._anchor_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

        self._clip_table.setColumnCount(5)
        self._clip_table.setHorizontalHeaderLabels(["视频", "时间", "摘要", "clip_id", "向量"])
        self._setup_table(self._clip_table)
        self._clip_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        self._binding_table.setColumnCount(7)
        self._binding_table.setHorizontalHeaderLabels(["视频", "时间", "摘要", "Anchor来源", "Anchor", "状态", "绑定ID"])
        self._setup_table(self._binding_table)
        self._binding_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        self._splitter.setSizes([440, 360, 520])

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
        except Exception as exc:
            logger.error(f"CrossModalPanel: list clips failed: {exc}", exc_info=True)
            self._clips = []
        self._populate_clip_table()

    def refresh_bindings(self) -> None:
        try:
            self._bindings = SQLiteStore().list_cross_modal()
        except Exception as exc:
            logger.error(f"CrossModalPanel: list bindings failed: {exc}", exc_info=True)
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
                _preview(row.get("display_content") or row.get("content", ""), 90),
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
            row["anchor_preview"] = anchor.get("display_content") or anchor.get("content", "")
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
        self._status.setText(f"已绑定 {map_id[:8]}...")

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
        self._status.setText(f"已修复绑定 {str(binding['id'])[:8]}...")

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
        except Exception as exc:
            logger.warning(f"CrossModalPanel: skip invalid chunks json {json_path}: {exc}")
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
                    "display_content": str(chunk.get("display_content") or chunk.get("content", "")),
                    "embedding_text": str(chunk.get("embedding_text") or chunk.get("content", "")),
                    "metadata": dict(chunk.get("metadata", {}) or {}),
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
