"""
dataflow_panel.py — Three-column data-flow visualisation panel.

Layout
------
┌──────────────────┬──────────────────────┬──────────────────┐
│  L1 Markdown     │   L2 JSON / Chunks   │  L3 Database     │
│                  │                      │                  │
│  [文件列表]      │  [Chunk 表格]        │  [向量数统计]    │
│  [打开/编辑]     │  [内联编辑 content]  │  [同步状态]      │
│  [转换→JSON]     │  [API 优化]          │  [同步到DB]      │
│                  │  [转换→MD]           │                  │
└──────────────────┴──────────────────────┴──────────────────┘

Top bar: API 优化触发按钮 + 全局进度条
"""

import json
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)

from app.utils import config as cfg
from app.utils.logger import logger


# ---------------------------------------------------------------------------
# Small worker thread for non-blocking file ops
# ---------------------------------------------------------------------------

class _ConvertWorker(QThread):
    """MD→JSON or JSON→MD conversion in background."""
    done = pyqtSignal(bool, str)   # (success, message)

    def __init__(self, direction: str, src: str, dst: str) -> None:
        super().__init__()
        self.direction = direction  # "md_to_json" | "json_to_md"
        self.src = src
        self.dst = dst

    def run(self) -> None:
        try:
            from app.models.md_json_converter import json_to_md, md_to_json

            if self.direction == "md_to_json":
                # Detect paired JSON for merge
                src_path = Path(self.src)
                base_json = src_path.parent / (src_path.stem + ".json")
                if not base_json.exists():
                    base_json = None
                result = md_to_json(self.src, base_json)
                dst_path = Path(self.dst)
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                dst_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                self.done.emit(True, f"已转换 {len(result.get('chunks', []))} 条 chunk → {self.dst}")

            else:  # json_to_md
                data = json.loads(Path(self.src).read_text(encoding="utf-8"))
                json_to_md(data, self.dst)
                self.done.emit(True, f"已转换 {data.get('chunk_count', 0)} 条 chunk → {self.dst}")

        except Exception as e:
            self.done.emit(False, str(e))


class _SyncWorker(QThread):
    """JSON → DB sync in background."""
    progress = pyqtSignal(int, int)
    done = pyqtSignal(bool, dict)

    def __init__(self, json_path: str) -> None:
        super().__init__()
        self.json_path = json_path

    def run(self) -> None:
        try:
            from app.models.json_db_sync import sync_json_to_db
            counts = sync_json_to_db(
                self.json_path,
                progress_cb=lambda done, total: self.progress.emit(done, total),
            )
            self.done.emit(True, counts)
        except Exception as e:
            self.done.emit(False, {"error": str(e)})


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

class DataflowPanel(QWidget):
    """Three-layer data flow visualisation and control panel."""

    chunks_loaded = pyqtSignal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_json_path: Optional[str] = None
        self._current_chunks: list = []
        self._convert_worker: Optional[_ConvertWorker] = None
        self._sync_worker: Optional[_SyncWorker] = None
        self._api_worker = None
        # Optional reference to SearchController for cache invalidation after edits
        self._search_ctrl = None
        self._build()
        self._refresh_file_list()

    def set_search_controller(self, ctrl) -> None:
        """Inject a SearchController so edits auto-invalidate the search cache."""
        self._search_ctrl = ctrl

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Top bar — API optimize + progress
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("数据流管理"))

        self._api_optimize_btn = QPushButton("AI 优化选中 JSON")
        self._api_optimize_btn.setStyleSheet("font-weight: bold; background-color: #2a6fdb; color: white;")
        self._api_optimize_btn.clicked.connect(self._run_api_optimize)
        top_bar.addWidget(self._api_optimize_btn)

        self._auto_sync_chk = QCheckBox("优化后自动同步到 DB")
        self._auto_sync_chk.setChecked(False)
        top_bar.addWidget(self._auto_sync_chk)

        top_bar.addStretch()
        root.addLayout(top_bar)

        # Progress bar (hidden by default)
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setRange(0, 100)
        root.addWidget(self._progress_bar)

        # Log line
        self._log_label = QLabel()
        self._log_label.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(self._log_label)

        # Three-column splitter
        splitter = QSplitter(Qt.Horizontal)

        # === Left: L1 Markdown files ===
        left = self._build_left_column()
        splitter.addWidget(left)

        # === Middle: L2 JSON chunks ===
        mid = self._build_mid_column()
        splitter.addWidget(mid)

        # === Right: L3 Database ===
        right = self._build_right_column()
        splitter.addWidget(right)

        splitter.setSizes([260, 560, 220])
        root.addWidget(splitter, 1)

    def _build_left_column(self) -> QWidget:
        w = QGroupBox("L1  Markdown（人工可读）")
        v = QVBoxLayout(w)

        self._md_list = QListWidget()
        self._md_list.setAlternatingRowColors(True)
        self._md_list.currentItemChanged.connect(self._on_md_selected)
        v.addWidget(self._md_list)

        btn_row = QHBoxLayout()
        self._refresh_btn = QPushButton("刷新列表")
        self._refresh_btn.clicked.connect(self._refresh_file_list)
        self._open_md_btn = QPushButton("打开编辑")
        self._open_md_btn.clicked.connect(self._open_md_in_editor)
        btn_row.addWidget(self._refresh_btn)
        btn_row.addWidget(self._open_md_btn)
        v.addLayout(btn_row)

        self._md_to_json_btn = QPushButton("转换 → JSON")
        self._md_to_json_btn.setStyleSheet("background-color: #3a7d44; color: white;")
        self._md_to_json_btn.clicked.connect(self._convert_md_to_json)
        v.addWidget(self._md_to_json_btn)

        return w

    def _build_mid_column(self) -> QWidget:
        w = QGroupBox("L2  JSON（API 接口数据）")
        v = QVBoxLayout(w)

        # File selector
        file_row = QHBoxLayout()
        self._json_path_edit = QLineEdit()
        self._json_path_edit.setPlaceholderText("JSON 文件路径...")
        self._json_path_edit.setReadOnly(True)
        self._browse_json_btn = QPushButton("浏览")
        self._browse_json_btn.clicked.connect(self._browse_json)
        file_row.addWidget(self._json_path_edit)
        file_row.addWidget(self._browse_json_btn)
        v.addLayout(file_row)

        # Chunk table
        self._chunk_table = QTableWidget()
        self._chunk_table.setColumnCount(5)
        self._chunk_table.setHorizontalHeaderLabels(["#", "Heading path", "Block", "is_manual", "内容预览"])
        self._chunk_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._chunk_table.setEditTriggers(QAbstractItemView.DoubleClicked)
        self._chunk_table.horizontalHeader().setStretchLastSection(True)
        self._chunk_table.setColumnWidth(0, 40)
        self._chunk_table.setColumnWidth(1, 160)
        self._chunk_table.setColumnWidth(2, 60)
        self._chunk_table.setColumnWidth(3, 70)
        self._chunk_table.cellDoubleClicked.connect(self._on_chunk_double_clicked)
        v.addWidget(self._chunk_table, 1)

        # Chunk editor
        edit_group = QGroupBox("内联编辑（双击行打开）")
        edit_v = QVBoxLayout(edit_group)
        self._chunk_editor = QTextEdit()
        self._chunk_editor.setPlaceholderText("双击 chunk 行进行编辑...")
        self._chunk_editor.setMaximumHeight(120)
        edit_v.addWidget(self._chunk_editor)
        save_edit_row = QHBoxLayout()
        self._save_chunk_btn = QPushButton("保存修改")
        self._save_chunk_btn.clicked.connect(self._save_chunk_edit)
        save_edit_row.addStretch()
        save_edit_row.addWidget(self._save_chunk_btn)
        edit_v.addLayout(save_edit_row)
        v.addWidget(edit_group)

        # Action buttons
        btn_row = QHBoxLayout()
        self._reload_json_btn = QPushButton("重新加载")
        self._reload_json_btn.clicked.connect(self._reload_current_json)
        self._json_to_md_btn = QPushButton("转换 → MD")
        self._json_to_md_btn.clicked.connect(self._convert_json_to_md)
        btn_row.addWidget(self._reload_json_btn)
        btn_row.addWidget(self._json_to_md_btn)
        v.addLayout(btn_row)

        return w

    def _build_right_column(self) -> QWidget:
        w = QGroupBox("L3  数据库（检索引擎）")
        v = QVBoxLayout(w)

        self._db_stats_label = QLabel("向量数量：—\n上次同步：—")
        self._db_stats_label.setWordWrap(True)
        v.addWidget(self._db_stats_label)

        self._sync_status_label = QLabel("")
        self._sync_status_label.setWordWrap(True)
        self._sync_status_label.setStyleSheet("color: #888; font-size: 11px;")
        v.addWidget(self._sync_status_label)

        self._check_diff_btn = QPushButton("检查变更")
        self._check_diff_btn.clicked.connect(self._check_diff)
        v.addWidget(self._check_diff_btn)

        self._sync_btn = QPushButton("同步到 DB")
        self._sync_btn.setStyleSheet("font-weight: bold; background-color: #b85c00; color: white;")
        self._sync_btn.clicked.connect(self._sync_to_db)
        v.addWidget(self._sync_btn)

        self._sync_progress = QProgressBar()
        self._sync_progress.setVisible(False)
        v.addWidget(self._sync_progress)

        v.addStretch()

        # DB stats refresh
        self._refresh_db_stats()
        return w

    # ------------------------------------------------------------------
    # Left column — MD file list
    # ------------------------------------------------------------------

    def _refresh_file_list(self) -> None:
        self._md_list.clear()
        exports_root = cfg.abs_path(cfg.get("exports.path", "data/exports"))
        md_files = sorted(exports_root.rglob("*.chunks.md"))
        for f in md_files:
            item = QListWidgetItem(f.name)
            item.setData(Qt.UserRole, str(f))
            item.setToolTip(str(f))
            self._md_list.addItem(item)
        self._log(f"找到 {len(md_files)} 个 MD chunk 文件")

    def _on_md_selected(self, current: Optional[QListWidgetItem], _) -> None:
        if current is None:
            return
        md_path = current.data(Qt.UserRole)
        # Auto-load paired JSON if it exists
        json_path = md_path.replace(".chunks.md", ".chunks.json")
        if Path(json_path).exists():
            self._load_json(json_path)

    def _open_md_in_editor(self) -> None:
        item = self._md_list.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole)
        try:
            import os
            os.startfile(path)
        except AttributeError:
            import subprocess
            subprocess.Popen(["xdg-open", path])

    def _convert_md_to_json(self) -> None:
        item = self._md_list.currentItem()
        if not item:
            self._log("请先选择一个 MD 文件")
            return
        md_path = item.data(Qt.UserRole)
        dst = md_path.replace(".chunks.md", ".chunks.json")
        self._log(f"正在转换 {Path(md_path).name} → JSON...")
        self._start_convert("md_to_json", md_path, dst)

    # ------------------------------------------------------------------
    # Middle column — JSON chunk table
    # ------------------------------------------------------------------

    def _browse_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 JSON 文件",
            str(cfg.abs_path("data/exports")),
            "JSON Files (*.json)",
        )
        if path:
            self._load_json(path)

    def _load_json(self, path: str) -> None:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            self._log(f"加载失败: {e}")
            return

        self._current_json_path = path
        self._current_chunks = data.get("chunks", [])
        self._json_path_edit.setText(path)
        self._populate_chunk_table(self._current_chunks)
        source_file = data.get("source_file") or Path(path).name
        self.chunks_loaded.emit(_chunks_with_source_file(self._current_chunks, source_file))
        self._log(
            f"已加载 {len(self._current_chunks)} 条 chunk — {Path(path).name}"
        )
        self._refresh_db_stats()

    def _populate_chunk_table(self, chunks: list) -> None:
        self._chunk_table.setRowCount(0)
        for i, chunk in enumerate(chunks):
            self._chunk_table.insertRow(i)
            content_preview = str(chunk.get("display_content") or chunk.get("content", ""))[:80].replace("\n", " ")
            items = [
                QTableWidgetItem(str(i + 1)),
                QTableWidgetItem(str(chunk.get("heading_path", ""))),
                QTableWidgetItem(str(chunk.get("block_type", "text"))),
                QTableWidgetItem("✓" if chunk.get("is_manual") else ""),
                QTableWidgetItem(content_preview),
            ]
            items[0].setFlags(items[0].flags() & ~Qt.ItemIsEditable)
            items[3].setTextAlignment(Qt.AlignCenter)
            for col, item in enumerate(items):
                self._chunk_table.setItem(i, col, item)
        self._chunk_table.resizeRowsToContents()

    def _on_chunk_double_clicked(self, row: int, col: int) -> None:
        if row < len(self._current_chunks):
            content = self._current_chunks[row].get("display_content") or self._current_chunks[row].get("content", "")
            self._chunk_editor.setPlainText(content)
            self._chunk_editor.setProperty("editing_row", row)

    def _save_chunk_edit(self) -> None:
        row = self._chunk_editor.property("editing_row")
        if row is None or not (0 <= row < len(self._current_chunks)):
            self._log("没有正在编辑的 chunk")
            return
        new_content = self._chunk_editor.toPlainText().strip()
        if not new_content:
            return
        chunk_id = self._current_chunks[row].get("id", "")
        self._current_chunks[row]["content"] = new_content
        self._current_chunks[row]["display_content"] = new_content
        self._current_chunks[row]["embedding_text"] = new_content
        self._current_chunks[row]["is_manual"] = True
        # Update table preview
        self._chunk_table.item(row, 3).setText("✓")
        self._chunk_table.item(row, 4).setText(new_content[:80].replace("\n", " "))
        # Persist to JSON
        if self._current_json_path:
            self._write_current_json()
            self._log(f"Chunk #{row + 1} 已修改并保存（is_manual=true）")
        # Invalidate search cache so next search fetches updated content
        if chunk_id and self._search_ctrl:
            self._search_ctrl.invalidate_cache(chunk_id)

    def _write_current_json(self) -> None:
        if not self._current_json_path:
            return
        try:
            existing = json.loads(Path(self._current_json_path).read_text(encoding="utf-8"))
            existing["chunks"] = self._current_chunks
            existing["chunk_count"] = len(self._current_chunks)
            Path(self._current_json_path).write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            self._log(f"写入 JSON 失败: {e}")

    def _reload_current_json(self) -> None:
        if self._current_json_path:
            self._load_json(self._current_json_path)

    def _convert_json_to_md(self) -> None:
        if not self._current_json_path:
            self._log("请先加载一个 JSON 文件")
            return
        dst = self._current_json_path.replace(".json", ".md").replace(".chunks.md.json", ".chunks.md")
        if not dst.endswith(".md"):
            dst = self._current_json_path + ".md"
        self._log(f"正在转换 JSON → {Path(dst).name}...")
        self._start_convert("json_to_md", self._current_json_path, dst)

    # ------------------------------------------------------------------
    # Right column — DB status
    # ------------------------------------------------------------------

    def _refresh_db_stats(self) -> None:
        try:
            import sqlite3
            db_path = cfg.abs_path(cfg.get("vectors.db_path", "data/vectors/vectors.db"))
            if not db_path.exists():
                self._db_stats_label.setText("向量数量：数据库未创建")
                return
            conn = sqlite3.connect(str(db_path))
            count = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
            conn.close()
            self._db_stats_label.setText(f"向量数量：{count:,} 条\n数据库：{db_path.name}")
        except Exception as e:
            self._db_stats_label.setText(f"DB 状态获取失败: {e}")

    def _check_diff(self) -> None:
        if not self._current_json_path:
            self._log("请先加载 JSON 文件")
            return
        try:
            from app.models.json_db_sync import get_sync_status
            status = get_sync_status(self._current_json_path)
            self._sync_status_label.setText(
                f"新增: {status['new']}  变更: {status['changed']}  "
                f"未变: {status['unchanged']}  合计: {status['total']}"
            )
        except Exception as e:
            self._log(f"检查失败: {e}")

    def _sync_to_db(self) -> None:
        if not self._current_json_path:
            self._log("请先加载 JSON 文件")
            return
        if self._sync_worker and self._sync_worker.isRunning():
            self._log("同步正在进行中...")
            return
        self._sync_btn.setEnabled(False)
        self._sync_progress.setVisible(True)
        self._sync_progress.setValue(0)
        self._log("正在同步到数据库...")
        self._sync_worker = _SyncWorker(self._current_json_path)
        self._sync_worker.progress.connect(
            lambda d, t: self._sync_progress.setValue(int(d / t * 100) if t else 0)
        )
        self._sync_worker.done.connect(self._on_sync_done)
        self._sync_worker.start()

    def _on_sync_done(self, success: bool, counts: dict) -> None:
        self._sync_btn.setEnabled(True)
        self._sync_progress.setVisible(False)
        if success:
            self._log(
                f"同步完成 — 插入 {counts.get('inserted', 0)} "
                f"更新 {counts.get('updated', 0)} "
                f"跳过 {counts.get('skipped', 0)} "
                f"错误 {counts.get('errors', 0)}"
            )
        else:
            self._log(f"同步失败: {counts.get('error', '未知错误')}")
        self._refresh_db_stats()

    # ------------------------------------------------------------------
    # API Optimize
    # ------------------------------------------------------------------

    def _run_api_optimize(self) -> None:
        if not self._current_json_path:
            self._log("请先在中栏加载一个 JSON 文件")
            return
        if self._api_worker and self._api_worker.isRunning():
            self._log("优化任务正在进行中...")
            return

        api_cfg = cfg.get("api", {}) or {}
        if not api_cfg.get("api_key") and api_cfg.get("provider", "openai") != "custom":
            QMessageBox.warning(self, "未配置 API Key", "请先在「API 设置」标签页配置 API Key 后再运行优化。")
            return

        from app.workers.api_optimize_worker import ApiOptimizeWorker

        auto_sync = self._auto_sync_chk.isChecked()
        self._api_worker = ApiOptimizeWorker(
            chunks_json_path=self._current_json_path,
            auto_sync_db=auto_sync,
        )
        self._api_worker.log_message.connect(self._log)
        self._api_worker.batch_started.connect(
            lambda b, t: self._progress_bar.setValue(int((b - 1) / t * 100))
        )
        self._api_worker.optimize_done.connect(self._on_optimize_done)
        self._api_worker.error_occurred.connect(lambda e: self._log(f"错误: {e}"))
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._api_optimize_btn.setEnabled(False)
        self._api_worker.start()

    def _on_optimize_done(self, output_path: str) -> None:
        self._api_optimize_btn.setEnabled(True)
        self._progress_bar.setValue(100)
        self._log(f"优化完成 → {Path(output_path).name}")
        # Auto-load the optimized JSON into the middle column
        self._load_json(output_path)

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    def _start_convert(self, direction: str, src: str, dst: str) -> None:
        if self._convert_worker and self._convert_worker.isRunning():
            self._log("转换正在进行中...")
            return
        self._convert_worker = _ConvertWorker(direction, src, dst)
        self._convert_worker.done.connect(self._on_convert_done)
        self._convert_worker.start()

    def _on_convert_done(self, success: bool, msg: str) -> None:
        self._log(msg)
        if success:
            self._refresh_file_list()
            # If a JSON was just generated, load it
            if ".chunks.json" in msg:
                # Extract path from message
                for part in msg.split("→"):
                    candidate = part.strip()
                    if candidate.endswith(".json") and Path(candidate).exists():
                        self._load_json(candidate)
                        break

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        self._log_label.setText(msg)
        logger.info(f"DataflowPanel: {msg}")


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
