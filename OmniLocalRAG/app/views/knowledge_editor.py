"""
KnowledgeEditor: top-level window hosting PDF/Chunk/Video/Dataflow/API panels.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.controllers.ingest_controller import IngestController
from app.controllers.search_controller import SearchController
from app.utils.logger import logger


class KnowledgeEditor(QMainWindow):
    def __init__(
        self,
        ingest_ctrl: IngestController,
        search_ctrl: SearchController,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Omni-Local RAG - Knowledge Editor")
        self.resize(1200, 760)
        self._ingest = ingest_ctrl
        self._search = search_ctrl
        self._last_pdf_path: Optional[str] = None
        self._active_import_kind: Optional[str] = None
        self._build()

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        toolbar = QHBoxLayout()
        btn_import_file = QPushButton("导入文件")
        btn_import_file.clicked.connect(self._import_pdf)
        btn_import_video = QPushButton("导入视频")
        btn_import_video.clicked.connect(self._import_video)
        toolbar.addWidget(btn_import_file)
        toolbar.addWidget(btn_import_video)
        toolbar.addStretch()
        root.addLayout(toolbar)

        progress_row = QHBoxLayout()
        self._stage_label = QLabel("就绪")
        self._stage_label.setFixedWidth(240)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFixedHeight(18)
        self._progress_bar.setVisible(False)
        progress_row.addWidget(self._stage_label)
        progress_row.addWidget(self._progress_bar)
        root.addLayout(progress_row)

        self._tabs = QTabWidget()
        root.addWidget(self._tabs)

        from app.views.pdf_workbench import PDFWorkbench
        from app.views.chunk_workbench import ChunkWorkbench
        from app.views.video_workbench import VideoWorkbench
        from app.views.dataflow_panel import DataflowPanel
        from app.views.cross_modal_panel import CrossModalPanel
        from app.views.api_settings_panel import ApiSettingsPanel

        self._pdf_bench = PDFWorkbench(self._ingest, self._search, self)
        self._chunk_bench = ChunkWorkbench(self)
        self._video_bench = VideoWorkbench(self._ingest, self)
        self._dataflow_panel = DataflowPanel(self)
        self._cross_modal_panel = CrossModalPanel(self)
        self._api_settings_panel = ApiSettingsPanel(self)

        self._tabs.addTab(self._pdf_bench, "PDF纠错")
        self._tabs.addTab(self._chunk_bench, "分块管理")
        self._tabs.addTab(self._video_bench, "视频切片")
        self._tabs.addTab(self._dataflow_panel, "数据流管理")
        self._tabs.addTab(self._cross_modal_panel, "跨模态绑定")
        self._tabs.addTab(self._api_settings_panel, "API设置")
        self._video_bench.clip_created.connect(self._on_clip_created)
        self._video_bench.clips_loaded.connect(self._refresh_clip_combo)
        self._chunk_bench.chunks_available.connect(self._refresh_anchor_combo)
        self._dataflow_panel.chunks_loaded.connect(self._refresh_anchor_combo)
        self._video_bench.clip_created.connect(lambda _: self._cross_modal_panel.refresh_clips())
        self._video_bench.clips_loaded.connect(lambda _: self._cross_modal_panel.refresh_clips())
        self._chunk_bench.chunks_available.connect(lambda _: self._cross_modal_panel.refresh_anchors())
        self._dataflow_panel.chunks_loaded.connect(lambda _: self._cross_modal_panel.refresh_anchors())

        self._api_settings_panel.settings_saved.connect(
            lambda _: self._stage_label.setText("API 配置已保存")
        )

        align_group = QGroupBox("跨模态对齐绑定")
        align_layout = QHBoxLayout(align_group)
        align_layout.addWidget(QLabel("视频切片:"))
        self._clip_combo = QComboBox()
        self._clip_combo.setMinimumWidth(200)
        align_layout.addWidget(self._clip_combo)
        align_layout.addWidget(QLabel("关联 PDF 段落 (anchor_id):"))
        self._anchor_combo = QComboBox()
        self._anchor_combo.setMinimumWidth(200)
        align_layout.addWidget(self._anchor_combo)
        btn_bind = QPushButton("绑定")
        btn_bind.clicked.connect(self._bind_cross_modal)
        align_layout.addWidget(btn_bind)
        align_layout.addStretch()
        root.addWidget(align_group)

        self._ingest.progress.connect(self._on_progress)
        self._ingest.finished.connect(self._on_ingest_done)
        self._ingest.error_occurred.connect(self._on_error)
        self._ingest.degraded_mode.connect(self._on_degraded)
        if hasattr(self._ingest, "stage_changed"):
            self._ingest.stage_changed.connect(self._on_stage_changed)
        if hasattr(self._ingest, "page_progress"):
            self._ingest.page_progress.connect(self._on_page_progress)
        if hasattr(self._ingest, "parse_done"):
            self._ingest.parse_done.connect(self._on_parse_done)

    def _import_pdf(self) -> None:
        from PyQt5.QtWidgets import QDialog
        from app.views.pdf_import_panel import PdfImportPanel

        panel = PdfImportPanel(parent=self)
        if panel.exec_() != QDialog.Accepted:
            return
        path = panel.selected_file()
        if not path:
            return
        self._last_pdf_path = path
        self._active_import_kind = "file"
        self._stage_label.setText(f"正在转换: {path}")
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._ingest.ingest_file(path)
        self._pdf_bench.load_file(path)

    def _import_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "", "Video Files (*.mp4 *.mkv *.avi *.mov)"
        )
        if not path:
            return
        self._active_import_kind = "video"
        self._stage_label.setText(f"正在转写: {path}")
        self._video_bench.load_video(path)
        self._ingest.transcribe_video(path)
        self._tabs.setCurrentWidget(self._video_bench)

    def _bind_cross_modal(self) -> None:
        clip_id = self._clip_combo.currentData()
        anchor_id = self._anchor_combo.currentData()
        if not clip_id or not anchor_id:
            return
        from app.models.sqlite_store import SQLiteStore

        mid = SQLiteStore().insert_cross_modal(clip_id, anchor_id)
        logger.info(f"Cross-modal map created: {mid}")
        self._cross_modal_panel.refresh_bindings()
        self._stage_label.setText(f"已绑定: {clip_id[:8]}... -> {anchor_id[:8]}...")

    def _refresh_anchor_combo(self, chunks: list) -> None:
        self._anchor_combo.clear()
        for index, chunk in enumerate(chunks, start=1):
            if not isinstance(chunk, dict):
                continue
            anchor_id = str(chunk.get("anchor_id") or chunk.get("chunk_id") or "").strip()
            if not anchor_id:
                continue

            source_file = str(chunk.get("source_file") or "").strip()
            page = chunk.get("page", "")
            heading = str(chunk.get("heading_path") or "").replace("\n", " ").strip()
            content = str(chunk.get("content") or "").replace("\n", " ").strip()
            preview = content[:56] + ("..." if len(content) > 56 else "")

            label_parts = []
            if source_file:
                label_parts.append(source_file)
            label_parts.append(anchor_id)
            if page not in ("", None):
                label_parts.append(f"p{page}")
            if heading:
                label_parts.append(heading[:36])
            if preview:
                label_parts.append(preview)

            self._anchor_combo.addItem(" | ".join(label_parts), anchor_id)

        self._stage_label.setText(
            f"Anchor 列表已更新: {self._anchor_combo.count()} 个 chunk"
        )

    def _on_progress(self, current: int, total: int) -> None:
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
        if self._active_import_kind == "file":
            self._stage_label.setText(f"文件转换: {current}/{total}")
        else:
            self._stage_label.setText(f"处理中: {current}/{total}")

    def _on_ingest_done(self, success: bool) -> None:
        self._progress_bar.setVisible(False)
        if success:
            if self._active_import_kind == "file":
                self._stage_label.setText("文件转换完成，请到分块管理页点击“启动切块”")
                self._tabs.setCurrentWidget(self._pdf_bench)
                if self._last_pdf_path:
                    self._pdf_bench.load_converted_markdown(self._last_pdf_path)
                    self._chunk_bench.load_file(self._last_pdf_path, source_type="pdf")
            else:
                self._stage_label.setText("导入完成")
        else:
            self._stage_label.setText("导入失败")
        self._active_import_kind = None

    def _on_error(self, msg: str) -> None:
        from PyQt5.QtWidgets import QMessageBox

        self._progress_bar.setVisible(False)
        QMessageBox.warning(self, "错误", msg)
        self._stage_label.setText(f"错误: {msg}")

    def _on_degraded(self, attempted: str, fallback: str, reason: str) -> None:
        from PyQt5.QtWidgets import QMessageBox

        QMessageBox.information(self, "解析器降级", reason)
        self._stage_label.setText(f"降级: {reason}")

    def _on_stage_changed(self, stage: str) -> None:
        self._stage_label.setText(stage)

    def _on_page_progress(self, current: int, total: int) -> None:
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
            self._stage_label.setText(f"解析页面: {current}/{total}")

    def _on_parse_done(self, parser_name: str, num_blocks: int) -> None:
        self._stage_label.setText(f"转换完成 ({parser_name})，已生成 Markdown")

    def _on_clip_created(self, clip: dict) -> None:
        clip_id = clip.get("id")
        if not clip_id:
            return
        self._add_clip_to_combo(clip)
        self._stage_label.setText(f"切片已创建: {clip_id[:8]}...")

    def _refresh_clip_combo(self, clips: list) -> None:
        self._clip_combo.clear()
        for clip in clips:
            self._add_clip_to_combo(clip)
        self._stage_label.setText(f"视频切片列表已更新: {self._clip_combo.count()} 个 clip")

    def _add_clip_to_combo(self, clip: dict) -> None:
        clip_id = clip.get("id")
        if not clip_id:
            return
        for index in range(self._clip_combo.count()):
            if self._clip_combo.itemData(index) == clip_id:
                return
        start = clip.get("start", clip.get("start_sec", 0)) or 0
        end = clip.get("end", clip.get("end_sec", 0)) or 0
        video_file = str(clip.get("video_file") or "").strip()
        summary = str(clip.get("summary") or clip.get("semantic_summary") or "").replace("\n", " ").strip()
        label_parts = []
        if video_file:
            label_parts.append(video_file)
        try:
            label_parts.append(f"{float(start):.1f}s-{float(end):.1f}s")
        except (TypeError, ValueError):
            label_parts.append(f"{start}s-{end}s")
        if summary:
            label_parts.append(summary[:48] + ("..." if len(summary) > 48 else ""))
        label_parts.append(f"{clip_id[:8]}...")
        label = " | ".join(label_parts)
        self._clip_combo.addItem(label, clip_id)
