"""PDFWorkbench — PDF/source view plus converted Markdown correction editor."""

import re
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QEvent, QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QPainter, QPen, QPixmap, QColor, QFont
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
    QSpinBox,
)

from app.controllers.ingest_controller import IngestController
from app.controllers.search_controller import SearchController
from app.utils import config as cfg
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
        self._total_pages: int = 0
        self._current_page: int = 0
        self._fitz_doc = None               # cached fitz.Document
        self._zoom_factor: float = 1.0
        self._fit_page_enabled: bool = True
        self._build()

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # Top toolbar
        tb = QHBoxLayout()
        self._file_label = QLabel("未加载文件")
        self._file_label.setStyleSheet("color: #aaa; font-size: 11px;")
        tb.addWidget(self._file_label)
        tb.addStretch()
        self._save_full_btn = QPushButton("保存 Markdown")
        self._save_full_btn.setToolTip("保存转换后的 Markdown，并生成 PDF 页图到关联目录")
        self._save_full_btn.clicked.connect(self._save_full_markdown)
        tb.addWidget(self._save_full_btn)
        root.addLayout(tb)

        # Two-pane splitter: PDF source + raw Markdown correction.
        splitter = QSplitter(Qt.Horizontal)

        # ── Pane 1: PDF viewer ──────────────────────────────────────
        pdf_pane = QWidget()
        pdf_layout = QVBoxLayout(pdf_pane)
        pdf_layout.setContentsMargins(0, 0, 0, 0)
        pdf_layout.setSpacing(2)

        nav_row = QHBoxLayout()
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(32)
        self._prev_btn.clicked.connect(self._prev_page)
        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedWidth(32)
        self._next_btn.clicked.connect(self._next_page)
        self._page_spin = QSpinBox()
        self._page_spin.setMinimum(1)
        self._page_spin.setMaximum(1)
        self._page_spin.setFixedWidth(60)
        self._page_spin.valueChanged.connect(self._on_page_spin)
        self._page_total_lbl = QLabel("/ 1")
        nav_row.addWidget(self._prev_btn)
        nav_row.addWidget(self._page_spin)
        nav_row.addWidget(self._page_total_lbl)
        nav_row.addWidget(self._next_btn)
        self._zoom_out_btn = QPushButton("-")
        self._zoom_out_btn.setFixedWidth(28)
        self._zoom_out_btn.clicked.connect(self._zoom_out)
        self._zoom_in_btn = QPushButton("+")
        self._zoom_in_btn.setFixedWidth(28)
        self._zoom_in_btn.clicked.connect(self._zoom_in)
        self._actual_size_btn = QPushButton("100%")
        self._actual_size_btn.clicked.connect(self._actual_size)
        self._fit_page_btn = QPushButton("适应整页")
        self._fit_page_btn.clicked.connect(self._fit_page)
        self._zoom_label = QLabel("适应整页")
        self._zoom_label.setStyleSheet("color: #aaa;")
        nav_row.addWidget(self._zoom_out_btn)
        nav_row.addWidget(self._zoom_in_btn)
        nav_row.addWidget(self._actual_size_btn)
        nav_row.addWidget(self._fit_page_btn)
        nav_row.addWidget(self._zoom_label)
        nav_row.addStretch()
        pdf_layout.addLayout(nav_row)

        self._pdf_scroll = QScrollArea()
        self._pdf_label = _SelectablePDFLabel()
        self._pdf_label.selection_changed.connect(self._on_pdf_selection)
        self._pdf_scroll.setWidget(self._pdf_label)
        self._pdf_scroll.setWidgetResizable(False)
        self._pdf_scroll.viewport().installEventFilter(self)
        pdf_layout.addWidget(self._pdf_scroll)
        splitter.addWidget(pdf_pane)

        # ── Pane 2: Markdown editor ─────────────────────────────────
        md_pane = QWidget()
        md_layout = QVBoxLayout(md_pane)
        md_layout.setContentsMargins(0, 0, 0, 0)
        md_layout.setSpacing(2)

        md_header = QLabel("Markdown 全文编辑")
        md_header.setStyleSheet("font-weight: bold; padding: 2px;")
        md_layout.addWidget(md_header)

        self._md_editor = QTextEdit()
        self._md_editor.setPlaceholderText("加载文件后此处显示解析出的 Markdown 全文，可直接编辑…")
        mono = QFont("Consolas", 10)
        mono.setStyleHint(QFont.Monospace)
        self._md_editor.setFont(mono)
        md_layout.addWidget(self._md_editor)
        splitter.addWidget(md_pane)

        splitter.setSizes([520, 680])
        root.addWidget(splitter)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_file(self, file_path: str) -> None:
        """Load a PDF file: render page 1 and populate the Markdown editor."""
        self._current_file = file_path
        self._file_label.setText(Path(file_path).name)
        # Open fitz doc and cache
        try:
            import fitz  # type: ignore
            if self._fitz_doc:
                self._fitz_doc.close()
            self._fitz_doc = fitz.open(file_path)
            self._total_pages = len(self._fitz_doc)
        except ImportError:
            self._fitz_doc = None
            self._total_pages = 0
            logger.warning("PyMuPDF not available — PDF rendering disabled")
        except Exception as e:
            logger.error(f"PDF open error: {e}")
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

    # ------------------------------------------------------------------
    # Page navigation
    # ------------------------------------------------------------------

    def _prev_page(self) -> None:
        if self._current_page > 0:
            self._page_spin.setValue(self._current_page)  # triggers _on_page_spin

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
            self._pdf_label.setText("需安装 PyMuPDF (fitz) 才能渲染 PDF")
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
        except Exception as e:
            logger.error(f"PDF render error: {e}")

    # ------------------------------------------------------------------
    # Rubber-band → chunk selection
    # ------------------------------------------------------------------

    def _on_pdf_selection(self, rect: QRect) -> None:
        """Map the rubber-band selection rect on the PDF label to text in the
        Markdown editor and scroll to the nearest matching line.

        Strategy:
        1. Convert the pixel rect to PDF-coordinate space (points).
        2. Ask PyMuPDF for words inside that bbox.
        3. Build a search phrase from the found words.
        4. Use QTextEdit.find() to locate and scroll to the phrase.
        """
        if not self._fitz_doc or not self._current_page < self._total_pages:
            return
        if rect.width() < 5 or rect.height() < 5:
            return  # ignore accidental tiny clicks

        try:
            import fitz  # type: ignore
            page = self._fitz_doc[self._current_page]
            scale = self._effective_scale(page)
            if scale <= 0:
                return

            # Convert pixel rect → PDF points
            x0 = rect.left()   / scale
            y0 = rect.top()    / scale
            x1 = rect.right()  / scale
            y1 = rect.bottom() / scale
            bbox = fitz.Rect(x0, y0, x1, y1)

            # Extract words inside the selection (each word: x0,y0,x1,y1,text,…)
            words = page.get_text("words", clip=bbox)
            if not words:
                return

            phrase = " ".join(w[4] for w in words[:12])   # at most 12 words for searching
            if not phrase.strip():
                return

            # Scroll the Markdown editor to the first occurrence
            # Reset cursor to top first so find() searches from the beginning
            from PyQt5.QtGui import QTextCursor
            self._md_editor.moveCursor(QTextCursor.Start)
            found = self._md_editor.find(phrase)
            if not found:
                # Fallback: try with the first 3 words only
                short = " ".join(phrase.split()[:3])
                self._md_editor.moveCursor(QTextCursor.Start)
                self._md_editor.find(short)
            # Ensure the found position is scrolled into view
            self._md_editor.ensureCursorVisible()

        except Exception as e:
            logger.debug(f"PDF selection mapping failed: {e}")

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

    # ------------------------------------------------------------------
    # Save actions
    # ------------------------------------------------------------------

    def _save_full_markdown(self) -> None:
        """Write corrected raw markdown and page images."""
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
            logger.info(f"PDFWorkbench: markdown save OK — md={md_path.name}, images={img_dir}")
            QMessageBox.information(
                self,
                "完成",
                f"Markdown: {md_path}\n页图目录: {img_dir}",
            )
        except Exception as e:
            logger.error(f"PDFWorkbench full save failed: {e}", exc_info=True)
            QMessageBox.critical(self, "错误", f"保存失败:\n{e}")

    def _converted_md_path(self, source_file: str) -> Path:
        root = Path(cfg.get("exports.path", "data/exports"))
        root = root if root.is_absolute() else cfg.abs_path(str(root))
        return root / "pdf" / f"{self._safe_export_stem(source_file)}.converted.md"

    @staticmethod
    def _safe_export_stem(source_file: str) -> str:
        # Keep consistent with app.workers.ingest_worker._safe_export_stem.
        # Non-ASCII-only names are normalized to "document" in the ingest flow.
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


# ---------------------------------------------------------------------------
# Rubber-band PDF label
# ---------------------------------------------------------------------------

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
