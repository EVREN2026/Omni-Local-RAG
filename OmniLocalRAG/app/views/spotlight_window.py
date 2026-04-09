from typing import Optional

from PyQt5.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, QTimer
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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

        width = cfg.get("ui.window_width", 680)
        self.setFixedWidth(width)
        self.setMinimumHeight(60)

    def _build_ui(self) -> None:
        self._container = QFrame(self)
        self._container.setObjectName("SpotlightContainer")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._container)

        layout = QVBoxLayout(self._container)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

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

        # --- Answer area ---
        self._answer = QTextEdit()
        self._answer.setObjectName("AnswerArea")
        self._answer.setReadOnly(True)
        self._answer.setMinimumHeight(0)
        self._answer.setMaximumHeight(0)
        self._answer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
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
        self._answer.clear()
        self._answer.setMaximumHeight(200)
        self._clear_cards()
        self._stop_btn.show()
        self._cursor_timer.start()
        self._search_ctrl.search(query)
        MemoryWatcher().reset()

    def _on_token(self, token: str) -> None:
        cursor = self._answer.textCursor()
        # Remove trailing cursor char before appending
        text = self._answer.toPlainText()
        if text.endswith("▋"):
            cursor.deletePreviousChar()
        cursor.insertText(token)
        cursor.insertText("▋")
        self._answer.setTextCursor(cursor)
        self._answer.ensureCursorVisible()

    def _blink_cursor(self) -> None:
        text = self._answer.toPlainText()
        cursor = self._answer.textCursor()
        if text.endswith("▋"):
            cursor.deletePreviousChar()
        else:
            cursor.insertText("▋")
        self._answer.setTextCursor(cursor)

    def _on_context(self, results: list) -> None:
        from app.views.result_cards import build_card
        self._clear_cards()
        if not results:
            return
        self._cards_scroll.setMaximumHeight(320)
        for r in results:
            card = build_card(r, parent=self._cards_widget)
            if card:
                self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
        self.adjustSize()

    def _on_finished(self, success: bool) -> None:
        self._cursor_timer.stop()
        text = self._answer.toPlainText()
        if text.endswith("▋"):
            cursor = self._answer.textCursor()
            cursor.deletePreviousChar()
            self._answer.setTextCursor(cursor)
        self._stop_btn.hide()

    def _on_error(self, msg: str) -> None:
        self._on_finished(False)
        self._answer.setPlainText(f"[错误] {msg}")

    def _clear_cards(self) -> None:
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._cards_scroll.setMaximumHeight(0)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

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
