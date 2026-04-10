"""
KnowledgeEditor — top-level window that hosts PDF and Video workbenches
via a tab widget, plus a cross-modal alignment panel, API settings, and
the three-layer data flow visualisation panel.
"""
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
        self.setWindowTitle("Omni-Local RAG — 知识库编辑器")
        self.resize(1200, 760)
        self._ingest = ingest_ctrl
        self._search = search_ctrl
        self._last_pdf_path: Optional[str] = None
        self._build()

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Toolbar
        toolbar = QHBoxLayout()
        btn_import_pdf = QPushButton("导入文件")
        btn_import_pdf.clicked.connect(self._import_pdf)
        btn_import_md = QPushButton("导入 Markdown")
        btn_import_md.clicked.connect(self._import_markdown)
        btn_import_video = QPushButton("导入视频")
        btn_import_video.clicked.connect(self._import_video)
        toolbar.addWidget(btn_import_pdf)
        toolbar.addWidget(btn_import_md)
        toolbar.addWidget(btn_import_video)
        toolbar.addStretch()
        root.addLayout(toolbar)

        # Progress bar + stage label
        progress_row = QHBoxLayout()
        self._stage_label = QLabel("就绪")
        self._stage_label.setFixedWidth(220)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFixedHeight(18)
        self._progress_bar.setVisible(False)
        progress_row.addWidget(self._stage_label)
        progress_row.addWidget(self._progress_bar)
        root.addLayout(progress_row)

        # Tabs
        self._tabs = QTabWidget()
        root.addWidget(self._tabs)

        from app.views.pdf_workbench import PDFWorkbench
        from app.views.video_workbench import VideoWorkbench
        from app.views.dataflow_panel import DataflowPanel
        from app.views.api_settings_panel import ApiSettingsPanel

        self._pdf_bench = PDFWorkbench(self._ingest, self._search, self)
        self._video_bench = VideoWorkbench(self._ingest, self)
        self._dataflow_panel = DataflowPanel(self)
        self._api_settings_panel = ApiSettingsPanel(self)

        self._tabs.addTab(self._pdf_bench, "PDF 纠错")
        self._tabs.addTab(self._video_bench, "视频切片")
        self._tabs.addTab(self._dataflow_panel, "数据流管理")
        self._tabs.addTab(self._api_settings_panel, "API 设置")
        self._video_bench.clip_created.connect(self._on_clip_created)

        # Connect API settings save to dataflow panel refresh
        self._api_settings_panel.settings_saved.connect(
            lambda _: self._stage_label.setText("API 配置已保存")
        )

        # Cross-modal alignment panel
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

        # Connect ingest signals
        self._ingest.progress.connect(self._on_progress)
        self._ingest.finished.connect(self._on_ingest_done)
        self._ingest.error_occurred.connect(self._on_error)
        self._ingest.degraded_mode.connect(self._on_degraded)
        # New signals from upgraded IngestWorker
        if hasattr(self._ingest, "stage_changed"):
            self._ingest.stage_changed.connect(self._on_stage_changed)
        if hasattr(self._ingest, "page_progress"):
            self._ingest.page_progress.connect(self._on_page_progress)
        if hasattr(self._ingest, "parse_done"):
            self._ingest.parse_done.connect(self._on_parse_done)

    def _import_pdf(self) -> None:
        from app.views.pdf_import_panel import PdfImportPanel
        from PyQt5.QtWidgets import QDialog
        panel = PdfImportPanel(parent=self)
        if panel.exec_() != QDialog.Accepted:
            return
        path = panel.selected_file()
        if not path:
            return
        self._last_pdf_path = path
        self._stage_label.setText(f"正在转换: {path}")
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._ingest.ingest_file(path)
        self._pdf_bench.load_file(path)

    def _import_markdown(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Markdown 文件", "", "Markdown Files (*.md *.markdown)"
        )
        if path:
            self._stage_label.setText(f"正在导入 Markdown: {path}")
            self._progress_bar.setValue(0)
            self._progress_bar.setVisible(True)
            self._ingest.ingest_file(path)

    def _import_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "", "Video Files (*.mp4 *.mkv *.avi *.mov)"
        )
        if path:
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
        self._stage_label.setText(f"已绑定: {clip_id[:8]}… → {anchor_id[:8]}…")

    def _on_progress(self, current: int, total: int) -> None:
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
        self._stage_label.setText(f"向量化中: {current}/{total}")

    def _on_ingest_done(self, success: bool) -> None:
        self._progress_bar.setVisible(False)
        if success:
            self._stage_label.setText("导入完成")
            self._tabs.setCurrentWidget(self._pdf_bench)
            if self._last_pdf_path:
                self._pdf_bench.load_exported_chunks(self._last_pdf_path)
        else:
            self._stage_label.setText("导入失败")

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
        self._stage_label.setText(f"解析完成 ({parser_name})，共 {num_blocks} 个文本块")

    def _on_clip_created(self, clip: dict) -> None:
        clip_id = clip.get("id")
        if not clip_id:
            return
        start = clip.get("start", 0)
        end = clip.get("end", 0)
        label = f"{start:.1f}s–{end:.1f}s  {clip_id[:8]}…"
        self._clip_combo.addItem(label, clip_id)
        self._stage_label.setText(f"切片已创建: {clip_id[:8]}…")
