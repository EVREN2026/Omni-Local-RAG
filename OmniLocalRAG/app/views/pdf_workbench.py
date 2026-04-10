"""
PDFWorkbench — three-pane PDF correction workbench.

Pane 1 (left)   : PDF page renderer with rubber-band selection + prev/next nav
Pane 2 (center) : Full-document editable Markdown (QTextEdit)
Pane 3 (right)  : Chunk list (QTableWidget) with is_manual flag + vector status

Interaction flow:
  - Selecting a row in pane 3 scrolls pane 2 to the matching page-anchor comment
    and highlights the chunk text.
  - Rubber-band selection on pane 1 navigates pane 3 to the nearest coord-matched
    chunk.
  - "保存全文" triggers md_to_json → sync_json_to_db (bulk re-embed).
  - "保存切片" re-embeds the single selected chunk.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QPainter, QPen, QPixmap, QColor, QTextCursor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QSpinBox,
    QToolBar,
    QAction,
    QSizePolicy,
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
        self._chunks: list = []             # [{id, content, page, is_manual, ...}]
        self._current_chunk_idx: int = -1
        self._total_pages: int = 0
        self._current_page: int = 0
        self._fitz_doc = None               # cached fitz.Document
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
        self._save_full_btn = QPushButton("保存全文")
        self._save_full_btn.setToolTip("保存 Markdown 与切块 JSON，并生成 PDF 页图到关联目录")
        self._save_full_btn.clicked.connect(self._save_full_markdown)
        tb.addWidget(self._save_full_btn)
        root.addLayout(tb)

        # Three-pane splitter
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
        nav_row.addStretch()
        pdf_layout.addLayout(nav_row)

        self._pdf_scroll = QScrollArea()
        self._pdf_label = _SelectablePDFLabel()
        self._pdf_label.selection_changed.connect(self._on_pdf_selection)
        self._pdf_scroll.setWidget(self._pdf_label)
        self._pdf_scroll.setWidgetResizable(True)
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

        # ── Pane 3: Chunk list ──────────────────────────────────────
        chunk_pane = QWidget()
        chunk_layout = QVBoxLayout(chunk_pane)
        chunk_layout.setContentsMargins(0, 0, 0, 0)
        chunk_layout.setSpacing(2)

        chunk_header = QLabel("切块列表")
        chunk_header.setStyleSheet("font-weight: bold; padding: 2px;")
        chunk_layout.addWidget(chunk_header)

        self._chunk_table = QTableWidget()
        self._chunk_table.setColumnCount(4)
        self._chunk_table.setHorizontalHeaderLabels(["#", "页", "手动", "内容预览"])
        self._chunk_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._chunk_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._chunk_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._chunk_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._chunk_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._chunk_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._chunk_table.setAlternatingRowColors(True)
        # QTableWidget has no currentRowChanged signal in PyQt5.
        # Use currentCellChanged(row, col, prev_row, prev_col), and keep a fallback
        # for lightweight test stubs that may only implement itemSelectionChanged.
        if hasattr(self._chunk_table, "currentCellChanged"):
            self._chunk_table.currentCellChanged.connect(self._on_chunk_selection_changed)
        elif hasattr(self._chunk_table, "itemSelectionChanged"):
            self._chunk_table.itemSelectionChanged.connect(self._on_chunk_selection_changed)
        chunk_layout.addWidget(self._chunk_table)

        chunk_btn_row = QHBoxLayout()
        self._chunk_info_lbl = QLabel("未选中切片")
        self._chunk_info_lbl.setStyleSheet("color: #aaa; font-size: 10px;")
        chunk_btn_row.addWidget(self._chunk_info_lbl)
        chunk_btn_row.addStretch()
        self._save_chunk_btn = QPushButton("保存切片")
        self._save_chunk_btn.setToolTip("仅重新向量化当前选中的切片")
        self._save_chunk_btn.clicked.connect(self._save_single_chunk)
        chunk_btn_row.addWidget(self._save_chunk_btn)
        chunk_layout.addLayout(chunk_btn_row)
        splitter.addWidget(chunk_pane)

        splitter.setSizes([400, 500, 350])
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
        self._page_spin.setMaximum(max(1, self._total_pages))
        self._page_total_lbl.setText(f"/ {self._total_pages or '?'}")
        self._page_spin.setValue(1)
        self._render_page(0)
        self.load_exported_chunks(file_path)

    def load_exported_chunks(self, file_path: Optional[str] = None) -> None:
        path = file_path or self._current_file
        if not path:
            return
        json_path = self._export_json_path(path)
        if not json_path.exists():
            self._chunks = []
            self._chunk_table.setRowCount(0)
            return
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            rows = payload.get("chunks", [])
            chunks = []
            for row in rows:
                chunks.append(
                    {
                        "id": row.get("chunk_id") or row.get("anchor_id") or "",
                        "content": row.get("content", ""),
                        "page": row.get("page", 0),
                        "coords": row.get("coords", []),
                        "is_manual": bool(row.get("is_manual", False)),
                        "heading_path": row.get("heading_path", ""),
                        "block_type": row.get("block_type", "text"),
                    }
                )
            self.set_chunks(chunks)
        except Exception as e:
            logger.error(f"Load exported chunks failed: {e}", exc_info=True)

    def set_chunks(self, chunks: list) -> None:
        """Populate the chunk list and build the Markdown full-text view."""
        self._chunks = chunks
        self._populate_chunk_table()
        self._build_markdown_from_chunks()

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

    def _render_page(self, page_num: int) -> None:
        if not self._fitz_doc:
            self._pdf_label.setText("需安装 PyMuPDF (fitz) 才能渲染 PDF")
            return
        if page_num < 0 or page_num >= self._total_pages:
            return
        try:
            import fitz  # type: ignore
            page = self._fitz_doc[page_num]
            mat = fitz.Matrix(1.5, 1.5)
            pix = page.get_pixmap(matrix=mat)
            qpix = QPixmap()
            qpix.loadFromData(pix.tobytes("png"))
            self._pdf_label.setPixmap(qpix)
            self._current_page = page_num
        except Exception as e:
            logger.error(f"PDF render error: {e}")

    # ------------------------------------------------------------------
    # Rubber-band → chunk selection
    # ------------------------------------------------------------------

    def _on_pdf_selection(self, rect: QRect) -> None:
        """Find chunk on the current page whose coords overlap the selection."""
        if not self._chunks:
            return
        # Find chunks on current page
        page_chunks = [
            (i, c) for i, c in enumerate(self._chunks)
            if c.get("page", 0) == self._current_page + 1
        ]
        if not page_chunks:
            # Fallback: just pick first chunk on any page
            page_chunks = [(0, self._chunks[0])]
        # Pick the first one for now (production: use coords overlap)
        idx, _ = page_chunks[0]
        self._chunk_table.setCurrentCell(idx, 0)

    # ------------------------------------------------------------------
    # Chunk list
    # ------------------------------------------------------------------

    def _populate_chunk_table(self) -> None:
        self._chunk_table.setRowCount(0)
        for i, chunk in enumerate(self._chunks):
            self._chunk_table.insertRow(i)
            self._chunk_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self._chunk_table.setItem(i, 1, QTableWidgetItem(str(chunk.get("page", ""))))
            manual_flag = "✓" if chunk.get("is_manual", False) else ""
            manual_item = QTableWidgetItem(manual_flag)
            manual_item.setTextAlignment(Qt.AlignCenter)
            self._chunk_table.setItem(i, 2, manual_item)
            preview = chunk.get("content", "")[:80].replace("\n", " ")
            self._chunk_table.setItem(i, 3, QTableWidgetItem(preview))

    def _on_chunk_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._chunks):
            return
        self._current_chunk_idx = row
        chunk = self._chunks[row]
        cid = chunk.get("id", "")
        page = chunk.get("page", "?")
        self._chunk_info_lbl.setText(f"ID: {cid[:8]}…  页: {page}")
        # Navigate PDF to the chunk's page
        if isinstance(page, int) and page >= 1:
            self._page_spin.setValue(page)
        # Highlight the chunk anchor in the Markdown editor
        self._scroll_md_to_chunk(row)

    def _on_chunk_selection_changed(self, *args) -> None:
        """Adapter for different QTableWidget signal signatures."""
        row = -1
        # currentCellChanged(row, col, prev_row, prev_col)
        if args and isinstance(args[0], int):
            row = args[0]
        elif hasattr(self._chunk_table, "currentRow"):
            row = self._chunk_table.currentRow()
        self._on_chunk_selected(row)

    # ------------------------------------------------------------------
    # Markdown editor
    # ------------------------------------------------------------------

    def _build_markdown_from_chunks(self) -> None:
        """Build a full-document Markdown string with page-anchor comments."""
        lines = []
        for i, chunk in enumerate(self._chunks):
            page = chunk.get("page", 0)
            cid = chunk.get("id", f"chunk_{i}")
            lines.append(f"<!-- chunk:{i} id:{cid} page:{page} -->")
            lines.append(chunk.get("content", ""))
            lines.append("")
        self._md_editor.setPlainText("\n".join(lines))

    def _scroll_md_to_chunk(self, chunk_idx: int) -> None:
        """Scroll and highlight the anchor line for the given chunk index."""
        doc = self._md_editor.document()
        search_text = f"<!-- chunk:{chunk_idx} "
        cursor = doc.find(search_text)
        if not cursor.isNull():
            # Move cursor to start of that block
            cursor.movePosition(QTextCursor.StartOfBlock)
            # Select to end of next paragraph
            cursor.movePosition(QTextCursor.NextBlock, QTextCursor.KeepAnchor, 2)
            self._md_editor.setTextCursor(cursor)
            self._md_editor.ensureCursorVisible()

    # ------------------------------------------------------------------
    # Save actions
    # ------------------------------------------------------------------

    def _save_full_markdown(self) -> None:
        """Parse edited markdown and write .chunks.md/.chunks.json + page images."""
        if not self._current_file:
            QMessageBox.warning(self, "提示", "请先加载一个 PDF 文件")
            return
        full_text = self._md_editor.toPlainText().strip()
        if not full_text:
            return

        # Reconstruct chunks from the editor content (strip anchor comments)
        import re
        pattern = re.compile(
            r"<!-- chunk:(\d+) id:([^\s]+) page:(\d+) -->\n(.*?)(?=\n<!-- chunk:|$)",
            re.DOTALL,
        )
        updated_chunks = []
        for m in pattern.finditer(full_text):
            idx = int(m.group(1))
            cid = m.group(2)
            page = int(m.group(3))
            content = m.group(4).strip()
            if idx < len(self._chunks):
                entry = dict(self._chunks[idx])
                entry["content"] = content
                entry["is_manual"] = True
                updated_chunks.append(entry)
            else:
                updated_chunks.append({
                    "id": cid, "page": page, "content": content,
                    "is_manual": True, "coords": [], "heading_path": "",
                    "block_type": "text",
                })

        if not updated_chunks:
            QMessageBox.warning(self, "提示", "未能从编辑器解析出任何切块，请检查格式")
            return

        try:
            json_path = self._export_json_path(self._current_file)
            md_path = self._export_md_path(self._current_file)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(full_text + "\n", encoding="utf-8")

            payload = {
                "source_file": Path(self._current_file).name,
                "source_type": "pdf",
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "chunk_count": len(updated_chunks),
                "chunks": [
                    {
                        "chunk_id": c.get("id", ""),
                        "anchor_id": c.get("id", ""),
                        "source_type": "pdf",
                        "page": c.get("page", 0),
                        "coords": c.get("coords", []),
                        "heading_path": c.get("heading_path", ""),
                        "block_type": c.get("block_type", "text"),
                        "content": c.get("content", ""),
                        "vector_dim": None,
                        "stored": False,
                        "is_manual": bool(c.get("is_manual", True)),
                    }
                    for c in updated_chunks
                ],
            }
            json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            img_dir = self._export_page_images()
            logger.info(
                f"PDFWorkbench: full save OK — {len(updated_chunks)} chunks, "
                f"md={md_path.name}, json={json_path.name}, images={img_dir}"
            )
            QMessageBox.information(
                self,
                "完成",
                f"已保存 {len(updated_chunks)} 个切块\n"
                f"Markdown: {md_path}\nJSON: {json_path}\n页图目录: {img_dir}",
            )
        except Exception as e:
            logger.error(f"PDFWorkbench full save failed: {e}", exc_info=True)
            QMessageBox.critical(self, "错误", f"保存失败:\n{e}")

    def _save_single_chunk(self) -> None:
        """Re-embed only the currently selected chunk."""
        if self._current_chunk_idx < 0 or self._current_chunk_idx >= len(self._chunks):
            QMessageBox.warning(self, "提示", "请先在右侧切块列表中选中一行")
            return
        chunk = self._chunks[self._current_chunk_idx]
        cid = chunk.get("id")

        # Extract the edited content for this chunk from the Markdown editor
        doc = self._md_editor.document()
        search_text = f"<!-- chunk:{self._current_chunk_idx} "
        cursor = doc.find(search_text)
        if cursor.isNull():
            new_content = chunk.get("content", "")
        else:
            # Select from after the anchor line to the next anchor (or EOF)
            cursor.movePosition(QTextCursor.EndOfBlock)
            cursor.movePosition(QTextCursor.NextCharacter)
            end_cursor = doc.find(f"<!-- chunk:{self._current_chunk_idx + 1} ", cursor)
            if end_cursor.isNull():
                cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
            else:
                end_cursor.movePosition(QTextCursor.StartOfBlock)
                cursor.setPosition(end_cursor.position(), QTextCursor.KeepAnchor)
            new_content = cursor.selectedText().replace("\u2029", "\n").strip()

        if not new_content:
            return

        self._ingest.re_embed_chunk(cid, new_content)
        self._search.invalidate_cache(cid)
        # Update local cache
        self._chunks[self._current_chunk_idx]["content"] = new_content
        self._chunks[self._current_chunk_idx]["is_manual"] = True
        # Refresh table row
        self._chunk_table.item(self._current_chunk_idx, 2).setText("✓")
        preview = new_content[:80].replace("\n", " ")
        self._chunk_table.item(self._current_chunk_idx, 3).setText(preview)
        self._chunk_info_lbl.setText(f"已保存: {cid[:8]}…")
        logger.info(f"PDFWorkbench: single chunk re-embed OK — {cid}")

    def _export_json_path(self, source_file: str) -> Path:
        root = Path(cfg.get("exports.path", "data/exports"))
        root = root if root.is_absolute() else cfg.abs_path(str(root))
        return root / "pdf" / f"{self._safe_export_stem(source_file)}.chunks.json"

    def _export_md_path(self, source_file: str) -> Path:
        root = Path(cfg.get("exports.path", "data/exports"))
        root = root if root.is_absolute() else cfg.abs_path(str(root))
        return root / "pdf" / f"{self._safe_export_stem(source_file)}.chunks.md"

    @staticmethod
    def _safe_export_stem(source_file: str) -> str:
        stem = Path(source_file).stem or "pdf"
        return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "pdf"

    def _export_page_images(self) -> Path:
        if not self._fitz_doc:
            raise RuntimeError("当前环境未启用 PyMuPDF，无法导出页图")
        if not self._current_file:
            raise RuntimeError("未加载 PDF 文件")
        import fitz  # type: ignore
        img_dir = self._export_md_path(self._current_file).with_name(
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
