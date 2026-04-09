"""
PDFWorkbench — left: PDF render, right: editable Markdown.
Supports rubber-band selection on the left to jump to the
corresponding text block on the right.
"""
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.controllers.ingest_controller import IngestController
from app.controllers.search_controller import SearchController
from app.utils.logger import logger


class PDFWorkbench(QWidget):
    def __init__(
        self,
        ingest_ctrl: IngestController,
        search_ctrl: SearchController,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ingest = ingest_ctrl
        self._search = search_ctrl
        self._current_file: Optional[str] = None
        self._chunks: list = []          # [{id, content, page, ...}]
        self._current_chunk_id: Optional[str] = None
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Horizontal)

        # Left: PDF page viewer
        self._pdf_scroll = QScrollArea()
        self._pdf_label = _SelectablePDFLabel()
        self._pdf_label.selection_changed.connect(self._on_pdf_selection)
        self._pdf_scroll.setWidget(self._pdf_label)
        self._pdf_scroll.setWidgetResizable(True)
        splitter.addWidget(self._pdf_scroll)

        # Right: Markdown editor + save button
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._editor = QTextEdit()
        self._editor.setPlaceholderText("选择左侧文本块后，此处显示对应 Markdown…")
        right_layout.addWidget(self._editor)

        btn_row = QHBoxLayout()
        self._save_btn = QPushButton("保存并更新向量")
        self._save_btn.clicked.connect(self._save)
        self._chunk_label = QLabel("未选中切片")
        btn_row.addWidget(self._chunk_label)
        btn_row.addStretch()
        btn_row.addWidget(self._save_btn)
        right_layout.addLayout(btn_row)

        splitter.addWidget(right)
        splitter.setSizes([550, 550])
        layout.addWidget(splitter)

    def load_file(self, file_path: str) -> None:
        self._current_file = file_path
        self._render_page(0)

    def _render_page(self, page_num: int) -> None:
        if not self._current_file:
            return
        try:
            import fitz  # type: ignore
            doc = fitz.open(self._current_file)
            if page_num >= len(doc):
                return
            page = doc[page_num]
            mat = fitz.Matrix(1.5, 1.5)
            pix = page.get_pixmap(matrix=mat)
            qpix = QPixmap()
            qpix.loadFromData(pix.tobytes("png"))
            self._pdf_label.setPixmap(qpix)
        except ImportError:
            self._pdf_label.setText("需安装 PyMuPDF (fitz) 才能渲染 PDF")
        except Exception as e:
            logger.error(f"PDF render error: {e}")

    def _on_pdf_selection(self, rect: QRect) -> None:
        """When user rubber-bands a region, find and show the nearest chunk."""
        if not self._chunks:
            return
        # Simplified: just show the first chunk near the selected y-range
        # In production, use coords from metadata to find precise match
        if self._chunks:
            chunk = self._chunks[0]
            self._current_chunk_id = chunk.get("id")
            self._editor.setPlainText(chunk.get("content", ""))
            self._chunk_label.setText(f"切片 ID: {self._current_chunk_id}")

    def set_chunks(self, chunks: list) -> None:
        self._chunks = chunks

    def _save(self) -> None:
        if not self._current_chunk_id:
            QMessageBox.warning(self, "提示", "请先在左侧选中一个文本块")
            return
        new_content = self._editor.toPlainText().strip()
        if not new_content:
            return
        self._ingest.re_embed_chunk(self._current_chunk_id, new_content)
        self._search.invalidate_cache(self._current_chunk_id)
        self._chunk_label.setText(f"已保存: {self._current_chunk_id[:8]}…")
        logger.info(f"PDFWorkbench: saved chunk {self._current_chunk_id}")


from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QPainter, QPen
from PyQt5.QtCore import QPoint


class _SelectablePDFLabel(QLabel):
    selection_changed = pyqtSignal(QRect)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._start: Optional[QPoint] = None
        self._end: Optional[QPoint] = None
        self._rect: Optional[QRect] = None
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._start = event.pos()
            self._end = event.pos()

    def mouseMoveEvent(self, event) -> None:
        if self._start:
            self._end = event.pos()
            self._rect = QRect(self._start, self._end).normalized()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._start and self._end:
            self._rect = QRect(self._start, self._end).normalized()
            self.selection_changed.emit(self._rect)
            self._start = None

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._rect:
            painter = QPainter(self)
            pen = QPen(Qt.blue, 2, Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(self._rect)
