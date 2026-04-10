from typing import Optional

from PyQt5.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRect, Qt, QTimer, QUrl
from PyQt5.QtGui import QKeyEvent, QTextDocument
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.controllers.memory_watcher import MemoryWatcher
from app.controllers.search_controller import SearchController
from app.utils import config as cfg


class SpotlightWindow(QWidget):
    """
    Frameless, always-on-top search window.
    Shown/hidden via Alt+Space; never destroyed while app is running.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._search_ctrl = SearchController(self)
        self._anim: Optional[QPropertyAnimation] = None
        self._cursor_timer = QTimer(self)
        self._cursor_visible = True
        self._dragging = False
        self._drag_offset = QPoint()

        self._setup_flags()
        self._build_ui()
        self._connect_signals()
        self._apply_styles()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_flags(self) -> None:
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)

        # Use configured width but enforce a comfortable minimum for rich content
        width = max(cfg.get("ui.window_width", 800), 800)
        self.setFixedWidth(width)
        self.setMinimumHeight(60)

    def _build_ui(self) -> None:
        self._container = QFrame(self)
        self._container.setObjectName("SpotlightContainer")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._container)

        layout = QVBoxLayout(self._container)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(8)

        self._drag_handle = QLabel("Omni-Local RAG")
        self._drag_handle.setObjectName("SpotlightDragHandle")
        self._drag_handle.setCursor(Qt.OpenHandCursor)
        layout.addWidget(self._drag_handle)

        # --- Input row ---
        input_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setObjectName("SearchInput")
        self._input.setPlaceholderText("问点什么…")
        self._input.setClearButtonEnabled(True)

        self._stop_btn = QPushButton("■")
        self._stop_btn.setObjectName("StopButton")
        self._stop_btn.setFixedSize(28, 28)
        self._stop_btn.setToolTip("停止生成")
        self._stop_btn.hide()

        input_row.addWidget(self._input)
        input_row.addWidget(self._stop_btn)
        layout.addLayout(input_row)

        # --- Answer area (QTextBrowser for rich HTML / table / image support) ---
        self._answer = QTextBrowser()
        self._answer.setObjectName("AnswerArea")
        self._answer.setReadOnly(True)
        self._answer.setMinimumHeight(0)
        self._answer.setMaximumHeight(0)
        self._answer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._answer.setOpenExternalLinks(True)
        self._answer.setOpenLinks(False)          # handle internally
        # Track raw answer text for cursor-blink manipulation
        self._answer_plain: str = ""
        layout.addWidget(self._answer)

        # --- Cards scroll area ---
        self._cards_scroll = QScrollArea()
        self._cards_scroll.setObjectName("CardsScroll")
        self._cards_scroll.setWidgetResizable(True)
        self._cards_scroll.setMinimumHeight(0)
        self._cards_scroll.setMaximumHeight(0)
        self._cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._cards_widget = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(6)
        self._cards_layout.addStretch()
        self._cards_scroll.setWidget(self._cards_widget)
        layout.addWidget(self._cards_scroll)

        self.adjustSize()
        self._container.installEventFilter(self)
        self._drag_handle.installEventFilter(self)

    def _connect_signals(self) -> None:
        self._input.returnPressed.connect(self._on_search)
        self._stop_btn.clicked.connect(self._search_ctrl.cancel)

        ctrl = self._search_ctrl
        ctrl.token_ready.connect(self._on_token)
        ctrl.context_ready.connect(self._on_context)
        ctrl.search_finished.connect(self._on_finished)
        ctrl.error_occurred.connect(self._on_error)

        self._cursor_timer.setInterval(500)
        self._cursor_timer.timeout.connect(self._blink_cursor)

    def _apply_styles(self) -> None:
        from pathlib import Path
        qss_path = Path(__file__).parent.parent.parent / "assets" / "styles" / "main.qss"
        if qss_path.exists():
            self.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Show / Hide
    # ------------------------------------------------------------------

    def toggle(self) -> None:
        if self.isVisible():
            self.hide_window()
        else:
            self.show_window()

    def show_window(self) -> None:
        self._position_on_active_screen()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()
        self._animate_opacity(0.0, cfg.get("ui.window_opacity", 0.95))
        MemoryWatcher().reset()

    def hide_window(self) -> None:
        self._animate_opacity(self.windowOpacity(), 0.0, on_done=self.hide)

    def _position_on_active_screen(self) -> None:
        cursor_pos = QApplication.desktop().cursor().pos()
        screen = QApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
        geom = screen.availableGeometry()
        x = geom.center().x() - self.width() // 2
        y = geom.top() + int(geom.height() * 0.25)
        self.move(x, y)

    def _animate_opacity(self, start: float, end: float, on_done=None) -> None:
        duration = cfg.get("ui.animation_ms", 80)
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(duration)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        if on_done:
            anim.finished.connect(on_done)
        self._anim = anim
        anim.start()

    # ------------------------------------------------------------------
    # Search flow
    # ------------------------------------------------------------------

    def _on_search(self) -> None:
        query = self._input.text().strip()
        if not query:
            return
        self._answer_plain = ""
        self._answer.clear()
        self._answer.setMaximumHeight(300)
        self._clear_cards()
        self._stop_btn.show()
        self._cursor_timer.start()
        self._search_ctrl.search(query)
        MemoryWatcher().reset()

    def _on_token(self, token: str) -> None:
        """Append a streaming token to the answer area (plain text accumulator)."""
        self._answer_plain += token
        # Render as plain text with trailing cursor indicator
        self._answer.setPlainText(self._answer_plain + "▋")
        self._answer.verticalScrollBar().setValue(
            self._answer.verticalScrollBar().maximum()
        )

    def _blink_cursor(self) -> None:
        text = self._answer.toPlainText()
        if text.endswith("▋"):
            self._answer.setPlainText(text[:-1])
        else:
            self._answer.setPlainText(text + "▋")

    def _on_context(self, results: list) -> None:
        from app.views.result_cards import build_card
        self._clear_cards()
        if not results:
            return
        self._cards_scroll.setMaximumHeight(340)
        for r in results:
            card = build_card(r, parent=self._cards_widget)
            if card:
                self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
        self.adjustSize()

    def _on_finished(self, success: bool) -> None:
        self._cursor_timer.stop()
        # Strip trailing cursor char
        plain = self._answer.toPlainText()
        if plain.endswith("▋"):
            plain = plain[:-1]
        self._answer_plain = plain

        # If the content contains HTML tags (tables, images) render as rich HTML
        if _contains_html(plain):
            html = _plain_to_html(plain)
            self._answer.setHtml(html)
        else:
            self._answer.setPlainText(plain)

        self._answer.verticalScrollBar().setValue(0)
        self._stop_btn.hide()

    def _on_error(self, msg: str) -> None:
        self._cursor_timer.stop()
        self._answer_plain = f"[错误] {msg}"
        self._answer.setPlainText(self._answer_plain)
        self._stop_btn.hide()

    def _clear_cards(self) -> None:
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._cards_scroll.setMaximumHeight(0)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        if obj not in (self._container, self._drag_handle):
            return super().eventFilter(obj, event)

        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            self._drag_handle.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return True

        if event.type() == QEvent.MouseMove and self._dragging and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
            return True

        if event.type() == QEvent.MouseButtonRelease and self._dragging:
            self._dragging = False
            self._drag_handle.setCursor(Qt.OpenHandCursor)
            event.accept()
            return True

        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide_window()
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        self.hide_window()
        super().focusOutEvent(event)

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide_window()


# ---------------------------------------------------------------------------
# HTML detection & conversion helpers
# ---------------------------------------------------------------------------

import re as _re


def _contains_html(text: str) -> bool:
    """Return True if text has any HTML tags (tables, images, etc.)."""
    return bool(_re.search(r"<(table|img|figure|div|p|ul|ol|li|br)[^>]*>", text, _re.IGNORECASE))


def _plain_to_html(text: str) -> str:
    """
    Wrap plain text that already contains HTML fragments in a styled document.
    Lines that are NOT HTML tags are converted to paragraphs; HTML blocks pass
    through unchanged.  Images gain a max-width style so they fit the window.
    """
    # Inject responsive image sizing and a table style
    style = """
    <style>
      body { font-family: 'Microsoft YaHei UI', sans-serif; font-size: 14px;
             color: #dbe5ef; line-height: 1.65; }
      img  { max-width: 100%; height: auto; border-radius: 6px;
             margin: 6px 0; display: block; }
      table { border-collapse: collapse; width: 100%; margin: 8px 0; }
      th, td { border: 1px solid rgba(255,255,255,22); padding: 5px 10px;
               text-align: left; }
      th { background: rgba(124,211,183,18); color: #8fd9c2; font-weight: 600; }
      tr:nth-child(even) { background: rgba(255,255,255,5); }
      a { color: #7cd3b7; }
      p { margin: 4px 0; }
    </style>
    """
    # Convert [图片] placeholder to a grey box label
    text = _re.sub(r"\[图片\]", '<span style="color:#8fa3b6;">[图片]</span>', text)

    # Split into lines; wrap non-HTML lines in <p>, leave HTML lines raw
    lines = text.split("\n")
    parts: list = []
    html_block = False
    buf: list = []

    def flush_buf():
        if buf:
            parts.append("<p>" + " ".join(buf) + "</p>")
            buf.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_buf()
            continue
        # Detect HTML block start/end
        if _re.match(r"<(table|img|figure|div|ul|ol)", stripped, _re.IGNORECASE):
            flush_buf()
            html_block = True
        if html_block:
            parts.append(line)
            if _re.search(r"</(table|figure|div|ul|ol)>", stripped, _re.IGNORECASE):
                html_block = False
        else:
            # Plain text line — accumulate for paragraph
            buf.append(stripped)

    flush_buf()

    body = "\n".join(parts)
    return f"<html><head>{style}</head><body>{body}</body></html>"
