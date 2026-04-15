import re
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QEvent, QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QLabel, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTextEdit, QWidget

from app.controllers.ingest_controller import IngestController
from app.controllers.search_controller import SearchController
from app.utils import config as cfg
from app.utils.logger import logger
from app.utils.ui_loader import load_ui, require_child


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
        self._total_pages = 0
        self._current_page = 0
        self._fitz_doc = None
        self._zoom_factor = 1.0
        self._fit_page_enabled = True
        self._build()

    def _build(self) -> None:
        load_ui(self, "pdf_workbench.ui")

        self._file_label = require_child(self, QLabel, "fileLabel", "PDFWorkbench UI")
        self._save_full_btn = require_child(self, QPushButton, "saveMarkdownButton", "PDFWorkbench UI")
        self._prev_btn = require_child(self, QPushButton, "prevPageButton", "PDFWorkbench UI")
        self._next_btn = require_child(self, QPushButton, "nextPageButton", "PDFWorkbench UI")
        self._page_spin = require_child(self, QSpinBox, "pageSpin", "PDFWorkbench UI")
        self._page_total_lbl = require_child(self, QLabel, "pageTotalLabel", "PDFWorkbench UI")
        self._zoom_out_btn = require_child(self, QPushButton, "zoomOutButton", "PDFWorkbench UI")
        self._zoom_in_btn = require_child(self, QPushButton, "zoomInButton", "PDFWorkbench UI")
        self._actual_size_btn = require_child(self, QPushButton, "actualSizeButton", "PDFWorkbench UI")
        self._fit_page_btn = require_child(self, QPushButton, "fitPageButton", "PDFWorkbench UI")
        self._zoom_label = require_child(self, QLabel, "zoomLabel", "PDFWorkbench UI")
        self._pdf_scroll = require_child(self, QScrollArea, "pdfScroll", "PDFWorkbench UI")
        self._md_editor = require_child(self, QTextEdit, "markdownEditor", "PDFWorkbench UI")

        self._file_label.setStyleSheet("color: #aaa; font-size: 11px;")
        self._page_spin.setMinimum(1)
        self._page_spin.setMaximum(1)
        self._page_spin.setFixedWidth(60)
        self._prev_btn.setFixedWidth(32)
        self._next_btn.setFixedWidth(32)
        self._zoom_out_btn.setFixedWidth(28)
        self._zoom_in_btn.setFixedWidth(28)
        self._zoom_label.setStyleSheet("color: #aaa;")

        self._pdf_label = _SelectablePDFLabel()
        self._pdf_label.selection_changed.connect(self._on_pdf_selection)
        self._pdf_scroll.setWidget(self._pdf_label)
        self._pdf_scroll.setWidgetResizable(False)
        self._pdf_scroll.viewport().installEventFilter(self)

        mono = QFont("Consolas", 10)
        mono.setStyleHint(QFont.Monospace)
        self._md_editor.setFont(mono)

        self._save_full_btn.clicked.connect(self._save_full_markdown)
        self._prev_btn.clicked.connect(self._prev_page)
        self._next_btn.clicked.connect(self._next_page)
        self._page_spin.valueChanged.connect(self._on_page_spin)
        self._zoom_out_btn.clicked.connect(self._zoom_out)
        self._zoom_in_btn.clicked.connect(self._zoom_in)
        self._actual_size_btn.clicked.connect(self._actual_size)
        self._fit_page_btn.clicked.connect(self._fit_page)

    def load_file(self, file_path: str) -> None:
        self._current_file = file_path
        self._file_label.setText(Path(file_path).name)
        try:
            import fitz  # type: ignore

            if self._fitz_doc:
                self._fitz_doc.close()
            self._fitz_doc = fitz.open(file_path)
            self._total_pages = len(self._fitz_doc)
        except ImportError:
            self._fitz_doc = None
            self._total_pages = 0
            logger.warning("PyMuPDF not available - PDF rendering disabled")
        except Exception as exc:
            logger.error(f"PDF open error: {exc}")
            self._fitz_doc = None
            self._total_pages = 0

        self._current_page = 0
        self._fit_page_enabled = True
        self._zoom_factor = 1.0
        self._zoom_label.setText("适应整页")
        self._page_spin.setMaximum(max(1, self._total_pages))
        self._page_total_lbl.setText(f"/ {self._total_pages or '?'}")
        self._page_spin.setValue(1)
        self._render_page(0)
        self.load_converted_markdown(file_path)

    def load_converted_markdown(self, file_path: Optional[str] = None) -> None:
        path = file_path or self._current_file
        if not path:
            return
        md_path = self._converted_md_path(path)
        if md_path.exists():
            self._md_editor.setPlainText(md_path.read_text(encoding="utf-8"))
            return
        self._md_editor.clear()

    def _prev_page(self) -> None:
        if self._current_page > 0:
            self._page_spin.setValue(self._current_page)

    def _next_page(self) -> None:
        if self._current_page < self._total_pages - 1:
            self._page_spin.setValue(self._current_page + 2)

    def _on_page_spin(self, value: int) -> None:
        self._render_page(value - 1)

    def _zoom_in(self) -> None:
        self._fit_page_enabled = False
        self._zoom_factor = min(4.0, self._zoom_factor * 1.2)
        self._render_page(self._current_page)

    def _zoom_out(self) -> None:
        self._fit_page_enabled = False
        self._zoom_factor = max(0.3, self._zoom_factor / 1.2)
        self._render_page(self._current_page)

    def _actual_size(self) -> None:
        self._fit_page_enabled = False
        self._zoom_factor = 1.0
        self._render_page(self._current_page)

    def _fit_page(self) -> None:
        self._fit_page_enabled = True
        self._render_page(self._current_page)

    def _effective_scale(self, page) -> float:
        if not self._fit_page_enabled:
            return self._zoom_factor
        viewport = self._pdf_scroll.viewport().size()
        view_w = max(1, viewport.width() - 8)
        view_h = max(1, viewport.height() - 8)
        page_rect = page.rect
        sx = view_w / max(page_rect.width, 1.0)
        sy = view_h / max(page_rect.height, 1.0)
        return max(0.1, min(sx, sy))

    def _render_page(self, page_num: int) -> None:
        if not self._fitz_doc:
            self._pdf_label.setText("需要安装 PyMuPDF (fitz) 才能渲染 PDF")
            return
        if page_num < 0 or page_num >= self._total_pages:
            return
        try:
            import fitz  # type: ignore

            page = self._fitz_doc[page_num]
            scale = self._effective_scale(page)
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat)
            qpix = QPixmap()
            qpix.loadFromData(pix.tobytes("png"))
            self._pdf_label.setPixmap(qpix)
            self._pdf_label.resize(qpix.size())
            self._current_page = page_num
            if self._fit_page_enabled:
                self._zoom_label.setText("适应整页")
            else:
                self._zoom_label.setText(f"{int(scale * 100)}%")
        except Exception as exc:
            logger.error(f"PDF render error: {exc}")

    def _on_pdf_selection(self, rect: QRect) -> None:
        if not self._fitz_doc or not self._current_page < self._total_pages:
            return
        if rect.width() < 5 or rect.height() < 5:
            return
        try:
            import fitz  # type: ignore
            from PyQt5.QtGui import QTextCursor

            page = self._fitz_doc[self._current_page]
            scale = self._effective_scale(page)
            if scale <= 0:
                return

            bbox = fitz.Rect(rect.left() / scale, rect.top() / scale, rect.right() / scale, rect.bottom() / scale)
            words = page.get_text("words", clip=bbox)
            if not words:
                return
            phrase = " ".join(word[4] for word in words[:12])
            if not phrase.strip():
                return
            self._md_editor.moveCursor(QTextCursor.Start)
            found = self._md_editor.find(phrase)
            if not found:
                short = " ".join(phrase.split()[:3])
                self._md_editor.moveCursor(QTextCursor.Start)
                self._md_editor.find(short)
            self._md_editor.ensureCursorVisible()
        except Exception as exc:
            logger.debug(f"PDF selection mapping failed: {exc}")

    def eventFilter(self, watched, event):
        if (
            watched is self._pdf_scroll.viewport()
            and event.type() == QEvent.Resize
            and self._fit_page_enabled
            and self._fitz_doc
            and self._total_pages > 0
        ):
            self._render_page(self._current_page)
        return super().eventFilter(watched, event)

    def _save_full_markdown(self) -> None:
        if not self._current_file:
            QMessageBox.warning(self, "提示", "请先加载一个 PDF 文件")
            return
        full_text = self._md_editor.toPlainText().strip()
        if not full_text:
            return
        try:
            md_path = self._converted_md_path(self._current_file)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(full_text + "\n", encoding="utf-8")
            img_dir = self._export_page_images()
            logger.info(f"PDFWorkbench: markdown save OK - md={md_path.name}, images={img_dir}")
            QMessageBox.information(self, "完成", f"Markdown: {md_path}\n页图目录: {img_dir}")
        except Exception as exc:
            logger.error(f"PDFWorkbench full save failed: {exc}", exc_info=True)
            QMessageBox.critical(self, "错误", f"保存失败:\n{exc}")

    def _converted_md_path(self, source_file: str) -> Path:
        root = Path(cfg.get("exports.path", "data/exports"))
        root = root if root.is_absolute() else cfg.abs_path(str(root))
        return root / "pdf" / f"{self._safe_export_stem(source_file)}.converted.md"

    @staticmethod
    def _safe_export_stem(source_file: str) -> str:
        stem = Path(source_file).stem or "pdf"
        return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "document"

    def _export_page_images(self) -> Path:
        if not self._fitz_doc:
            raise RuntimeError("当前环境未启用 PyMuPDF，无法导出页图")
        if not self._current_file:
            raise RuntimeError("未加载 PDF 文件")
        import fitz  # type: ignore

        img_dir = self._converted_md_path(self._current_file).with_name(
            f"{self._safe_export_stem(self._current_file)}_images"
        )
        img_dir.mkdir(parents=True, exist_ok=True)
        for idx in range(self._total_pages):
            page = self._fitz_doc[idx]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
            out = img_dir / f"page_{idx + 1:04d}.png"
            pix.save(str(out))
        return img_dir


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
            pen = QPen(QColor(80, 160, 255), 2, Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(self._rect)
