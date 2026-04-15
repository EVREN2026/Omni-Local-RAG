"""
KnowledgeEditor: top-level window hosting document, chunk, video, dataflow,
cross-modal, and API settings panels.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import QFileDialog, QLabel, QMainWindow, QProgressBar, QPushButton, QTabWidget, QWidget

from app.controllers.ingest_controller import IngestController
from app.controllers.search_controller import SearchController
from app.utils.logger import logger
from app.utils.ui_loader import load_ui, require_child


class KnowledgeEditor(QMainWindow):
    def __init__(
        self,
        ingest_ctrl: IngestController,
        search_ctrl: SearchController,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ingest = ingest_ctrl
        self._search = search_ctrl
        self._last_pdf_path: Optional[str] = None
        self._active_import_kind: Optional[str] = None
        self._build()

    def _build(self) -> None:
        load_ui(self, "knowledge_editor.ui")

        self._stage_label = require_child(self, QLabel, "stageLabel", "KnowledgeEditor UI")
        self._progress_bar = require_child(self, QProgressBar, "progressBar", "KnowledgeEditor UI")
        self._tabs = require_child(self, QTabWidget, "tabsWidget", "KnowledgeEditor UI")
        btn_import_file = require_child(self, QPushButton, "importFileButton", "KnowledgeEditor UI")
        btn_import_video = require_child(self, QPushButton, "importVideoButton", "KnowledgeEditor UI")

        self.setWindowTitle("Omni-Local RAG - Knowledge Editor")
        self.resize(1200, 760)

        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setVisible(False)

        btn_import_file.clicked.connect(self._import_pdf)
        btn_import_video.clicked.connect(self._import_video)

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
        self._dataflow_panel.set_search_controller(self._search)
        self._cross_modal_panel = CrossModalPanel(self)
        self._api_settings_panel = ApiSettingsPanel(self)

        self._tabs.addTab(self._pdf_bench, "PDF纠错")
        self._tabs.addTab(self._chunk_bench, "分块管理")
        self._tabs.addTab(self._video_bench, "视频切片")
        self._tabs.addTab(self._dataflow_panel, "数据流管理")
        self._tabs.addTab(self._cross_modal_panel, "跨模态绑定")
        self._tabs.addTab(self._api_settings_panel, "API设置")

        self._video_bench.clip_created.connect(lambda _: self._cross_modal_panel.refresh_clips())
        self._video_bench.clips_loaded.connect(lambda _: self._cross_modal_panel.refresh_clips())
        self._chunk_bench.chunks_available.connect(lambda _: self._cross_modal_panel.refresh_anchors())
        self._dataflow_panel.chunks_loaded.connect(lambda _: self._cross_modal_panel.refresh_anchors())
        self._api_settings_panel.settings_saved.connect(lambda _: self._stage_label.setText("API 配置已保存"))

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
            self,
            "选择视频文件",
            "",
            "Video Files (*.mp4 *.mkv *.avi *.mov)",
        )
        if not path:
            return
        self._active_import_kind = "video"
        self._stage_label.setText(f"正在转写: {path}")
        self._video_bench.load_video(path)
        self._ingest.transcribe_video(path)
        self._tabs.setCurrentWidget(self._video_bench)

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
                self._stage_label.setText("文件转换完成，请到分块管理页继续处理。")
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
        del attempted, fallback
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
        del num_blocks
        self._stage_label.setText(f"转换完成 ({parser_name})，已生成 Markdown")
