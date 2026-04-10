"""
KnowledgeEditor — top-level window that hosts PDF and Video workbenches
via a tab widget, plus a cross-modal alignment panel.
"""
from typing import Optional

from PyQt5.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
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
        self.resize(1100, 700)
        self._ingest = ingest_ctrl
        self._search = search_ctrl
        self._build()

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Toolbar
        toolbar = QHBoxLayout()
        btn_import_pdf = QPushButton("导入 PDF")
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

        # Progress label
        self._status_label = QLabel("就绪")
        root.addWidget(self._status_label)

        # Tabs
        self._tabs = QTabWidget()
        root.addWidget(self._tabs)

        from app.views.pdf_workbench import PDFWorkbench
        from app.views.video_workbench import VideoWorkbench

        self._pdf_bench = PDFWorkbench(self._ingest, self._search, self)
        self._video_bench = VideoWorkbench(self._ingest, self)
        self._tabs.addTab(self._pdf_bench, "PDF 纠错")
        self._tabs.addTab(self._video_bench, "视频切片")

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

    def _import_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 PDF 文件", "", "PDF Files (*.pdf)"
        )
        if path:
            self._status_label.setText(f"正在导入: {path}")
            self._ingest.ingest_file(path)
            self._pdf_bench.load_file(path)

    def _import_markdown(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Markdown 文件", "", "Markdown Files (*.md *.markdown)"
        )
        if path:
            self._status_label.setText(f"正在导入 Markdown: {path}")
            self._ingest.ingest_file(path)

    def _import_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "", "Video Files (*.mp4 *.mkv *.avi *.mov)"
        )
        if path:
            self._status_label.setText(f"正在转写: {path}")
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
        self._status_label.setText(f"已绑定: {clip_id[:8]}… → {anchor_id[:8]}…")

    def _on_progress(self, current: int, total: int) -> None:
        self._status_label.setText(f"索引中: {current}/{total}")

    def _on_ingest_done(self, success: bool) -> None:
        self._status_label.setText("导入完成" if success else "导入失败")

    def _on_error(self, msg: str) -> None:
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.warning(self, "错误", msg)
        self._status_label.setText(f"错误: {msg}")

    def _on_degraded(self, msg: str) -> None:
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(self, "降级提示", msg)
        self._status_label.setText(msg)
