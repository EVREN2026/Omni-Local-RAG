import os
from html import escape as _html_escape
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRect, Qt, QTimer
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
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.controllers.memory_watcher import MemoryWatcher
from app.controllers.search_controller import SearchController
from app.utils import config as cfg
from app.utils.logger import logger
from app.utils.ui_loader import load_ui, optional_child, require_child


class AnswerTextBrowser(QTextBrowser):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._zoom_steps = 0
        self._zoom_min = -4
        self._zoom_max = 10

    def wheelEvent(self, event) -> None:
        if not (event.modifiers() & Qt.ControlModifier):
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        step = 1 if delta > 0 else -1
        next_steps = max(self._zoom_min, min(self._zoom_max, self._zoom_steps + step))
        applied = next_steps - self._zoom_steps
        if applied == 0:
            event.accept()
            return
        self.zoomIn(applied)
        self._zoom_steps = next_steps
        event.accept()


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
        self._resizing = False
        self._resize_edges: tuple[bool, bool, bool, bool] = (False, False, False, False)
        self._resize_start_geom = self.geometry()
        self._resize_start_global = QPoint()
        self._resize_margin = 10
        self._user_resized = False
        self._window_width = int(cfg.get("ui.window_width", 1040))
        self._default_window_height = int(cfg.get("ui.window_height", 720))
        self._results_panel_width = 372
        self._compact_results_height = 248
        self._responsive_breakpoint = 1040
        self._selected_card: Optional[QWidget] = None
        self._selected_result: Optional[dict] = None
        self._context_results: list = []
        self._result_cards: list[QWidget] = []
        self._active_query = ""

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

        self.resize(self._window_width, self._default_window_height)
        self.setMinimumSize(920, 560)

    def _build_ui(self) -> None:
        load_ui(self, "spotlight_window.ui")

        self._container = require_child(self, QFrame, "containerFrame", "Spotlight UI")
        self._drag_handle = require_child(self, QLabel, "dragHandleLabel", "Spotlight UI")
        self._input = require_child(self, QLineEdit, "searchInput", "Spotlight UI")
        self._stop_btn = require_child(self, QPushButton, "stopButton", "Spotlight UI")
        self._splitter = require_child(self, QSplitter, "resultSplitter", "Spotlight UI")
        self._timing_label = optional_child(self, QLabel, "timingLabel")
        self._body = optional_child(self, QWidget, "bodyWidget") or self._splitter
        self._results_panel = require_child(self, QWidget, "resultsPanel", "Spotlight UI")
        self._cards_label = require_child(self, QLabel, "cardsLabel", "Spotlight UI")
        self._cards_scroll = require_child(self, QScrollArea, "cardsScrollArea", "Spotlight UI")
        self._cards_widget = require_child(self, QWidget, "cardsContainerWidget", "Spotlight UI")
        self._cards_layout = self._cards_widget.layout()
        self._cards_empty = require_child(self, QLabel, "cardsEmptyLabel", "Spotlight UI")
        self._preview_panel = require_child(self, QWidget, "previewPanel", "Spotlight UI")
        self._knowledge_card = require_child(self, QFrame, "knowledgeCardFrame", "Spotlight UI")
        self._preview_title = require_child(self, QLabel, "previewTitleLabel", "Spotlight UI")
        self._preview_meta = optional_child(self, QLabel, "previewMetaLabel")
        self._copy_ref_btn = require_child(self, QPushButton, "copyReferenceButton", "Spotlight UI")
        self._open_source_btn = require_child(self, QPushButton, "openSourceButton", "Spotlight UI")
        answer_host = optional_child(self, QWidget, "answerHostWidget")

        # Keep legacy object names so the existing QSS keeps applying after .ui loading.
        self._container.setObjectName("SpotlightContainer")
        self._drag_handle.setObjectName("SpotlightDragHandle")
        self._input.setObjectName("SearchInput")
        self._stop_btn.setObjectName("StopButton")
        if self._timing_label is None:
            self._timing_label = QLabel("", self._container)
            self._timing_label.hide()
            container_layout = self._container.layout()
            if isinstance(container_layout, QVBoxLayout):
                container_layout.insertWidget(2, self._timing_label)
        self._timing_label.setObjectName("SearchTimingLabel")
        self._body.setObjectName("ResultBody")
        self._splitter.setObjectName("ResultSplitter")
        self._results_panel.setObjectName("ResultPane")
        self._preview_panel.setObjectName("ResultPane")
        self._cards_label.setObjectName("ResultSectionLabel")
        self._cards_scroll.setObjectName("CardsScroll")
        self._cards_empty.setObjectName("ResultEmptyHint")
        self._knowledge_card.setObjectName("KnowledgeCard")
        self._preview_title.setObjectName("KnowledgeCardTitle")
        if self._preview_meta is None:
            self._preview_meta = QLabel("", self._knowledge_card)
            self._preview_meta.hide()
            self._preview_meta.setWordWrap(True)
            card_layout = self._knowledge_card.layout()
            if isinstance(card_layout, QVBoxLayout):
                card_layout.insertWidget(1, self._preview_meta)
        self._preview_meta.setObjectName("KnowledgeCardMeta")
        self._copy_ref_btn.setObjectName("KnowledgeActionButton")
        self._open_source_btn.setObjectName("KnowledgeActionButton")

        self._drag_handle.setCursor(Qt.OpenHandCursor)
        self._results_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._cards_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._preview_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._knowledge_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._preview_title.setWordWrap(True)
        self._preview_meta.setWordWrap(True)
        self._preview_meta.hide()

        if answer_host is None:
            answer_host = QWidget(self._knowledge_card)
            answer_layout = QVBoxLayout(answer_host)
            answer_layout.setContentsMargins(0, 0, 0, 0)
            answer_layout.setSpacing(0)
            card_layout = self._knowledge_card.layout()
            if isinstance(card_layout, QVBoxLayout):
                card_layout.insertWidget(2, answer_host, 1)

        self._answer = AnswerTextBrowser(answer_host)
        self._answer.setObjectName("KnowledgeCardBody")
        self._answer.setReadOnly(True)
        self._answer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._answer.setOpenExternalLinks(True)
        self._answer.setOpenLinks(False)
        self._answer.setSearchPaths(_build_content_search_paths())
        answer_layout = answer_host.layout()
        answer_layout.addWidget(self._answer)
        self._answer_plain: str = ""

        self._copy_ref_btn.clicked.connect(self._copy_selected_reference)
        self._open_source_btn.clicked.connect(self._open_selected_source)

        self._splitter.setStretchFactor(0, 4)
        self._splitter.setStretchFactor(1, 7)
        self._sync_window_size(force=True)
        self.setMouseTracking(True)
        self._container.setMouseTracking(True)
        self._body.setMouseTracking(True)
        self.installEventFilter(self)
        self._container.installEventFilter(self)
        self._drag_handle.installEventFilter(self)
        self._input.installEventFilter(self)
        self._apply_responsive_layout(force=True)
        self._render_preview(streaming=False)

    def _connect_signals(self) -> None:
        self._input.returnPressed.connect(self._on_search)
        self._stop_btn.clicked.connect(self._search_ctrl.cancel)

        ctrl = self._search_ctrl
        ctrl.token_ready.connect(self._on_token)
        ctrl.context_ready.connect(self._on_context)
        ctrl.timings_ready.connect(self._on_timings)
        ctrl.search_finished.connect(self._on_finished)
        ctrl.error_occurred.connect(self._on_error)

        self._cursor_timer.setInterval(500)
        self._cursor_timer.timeout.connect(self._blink_cursor)

    def _apply_styles(self) -> None:
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
        self._refresh_size_constraints()
        if not self._user_resized:
            self._sync_window_size(force=True)
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

    def _refresh_size_constraints(self) -> None:
        return

    def _sync_window_size(self, force: bool = False) -> None:
        if self._user_resized and not force:
            return
        self.resize(self._window_width, self._default_window_height)
        self._apply_responsive_layout(force=True)

    def _update_body_visibility(self) -> None:
        self._body.show()
        self._results_panel.show()
        self._preview_panel.show()
        self._cards_empty.setVisible(not bool(self._context_results))
        self._apply_responsive_layout()

    def _apply_responsive_layout(self, force: bool = False) -> None:
        available_width = max(self.width() - 48, 640)
        compact = available_width < self._responsive_breakpoint
        target_orientation = Qt.Vertical if compact else Qt.Horizontal
        orientation_changed = self._splitter.orientation() != target_orientation

        if force or orientation_changed:
            self._splitter.setOrientation(target_orientation)

        if compact:
            self._results_panel.setMinimumWidth(0)
            self._results_panel.setMaximumWidth(16777215)
            self._results_panel.setMinimumHeight(210)
            self._results_panel.setMaximumHeight(self._compact_results_height)
            self._preview_panel.setMinimumWidth(0)
            self._preview_panel.setMinimumHeight(300)
            if force or orientation_changed:
                top_height = min(self._compact_results_height, max(210, int(self.height() * 0.34)))
                self._splitter.setSizes([top_height, max(320, self.height() - top_height - 40)])
        else:
            self._results_panel.setFixedWidth(self._results_panel_width)
            self._results_panel.setMinimumHeight(0)
            self._results_panel.setMaximumHeight(16777215)
            self._preview_panel.setMinimumWidth(420)
            if force or orientation_changed:
                self._splitter.setSizes(
                    [
                        self._results_panel_width,
                        max(560, self.width() - self._results_panel_width - 52),
                    ]
                )

    def _resize_hit_test(self, global_pos: QPoint) -> tuple[bool, bool, bool, bool]:
        local = self.mapFromGlobal(global_pos)
        x = local.x()
        y = local.y()
        left = x <= self._resize_margin
        right = x >= self.width() - self._resize_margin
        top = y <= self._resize_margin
        bottom = y >= self.height() - self._resize_margin
        return left, right, top, bottom

    def _resize_cursor_shape(self, edges: tuple[bool, bool, bool, bool]):
        left, right, top, bottom = edges
        if (left and top) or (right and bottom):
            return Qt.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.SizeBDiagCursor
        if left or right:
            return Qt.SizeHorCursor
        if top or bottom:
            return Qt.SizeVerCursor
        return None

    def _apply_resize_cursor(self, global_pos: QPoint) -> None:
        shape = self._resize_cursor_shape(self._resize_hit_test(global_pos))
        if shape is None:
            self.unsetCursor()
            self._container.unsetCursor()
            return
        self.setCursor(shape)
        self._container.setCursor(shape)

    def _perform_resize(self, global_pos: QPoint) -> None:
        if not self._resizing:
            return

        left, right, top, bottom = self._resize_edges
        dx = global_pos.x() - self._resize_start_global.x()
        dy = global_pos.y() - self._resize_start_global.y()
        geom = self._resize_start_geom

        min_w = self.minimumWidth()
        min_h = self.minimumHeight()

        new_left = geom.left()
        new_right = geom.right()
        new_top = geom.top()
        new_bottom = geom.bottom()

        if left:
            new_left += dx
            if new_right - new_left + 1 < min_w:
                new_left = new_right - min_w + 1
        if right:
            new_right += dx
            if new_right - new_left + 1 < min_w:
                new_right = new_left + min_w - 1
        if top:
            new_top += dy
            if new_bottom - new_top + 1 < min_h:
                new_top = new_bottom - min_h + 1
        if bottom:
            new_bottom += dy
            if new_bottom - new_top + 1 < min_h:
                new_bottom = new_top + min_h - 1

        self.setGeometry(QRect(QPoint(new_left, new_top), QPoint(new_right, new_bottom)))

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
        self._refresh_size_constraints()
        self._active_query = query
        self._answer_plain = ""
        self._context_results = []
        self._selected_result = None
        self._answer.clear()
        self._clear_cards()
        self._render_preview(streaming=False)
        self._timing_label.setText("\u6b63\u5728\u68c0\u7d22...")
        self._timing_label.show()
        self._stop_btn.show()
        self._cursor_timer.start()
        self._search_ctrl.search(query)
        MemoryWatcher().reset()

    def _on_token(self, token: str) -> None:
        """Append a streaming token to the answer area (plain text accumulator)."""
        self._answer_plain += token
        self._render_preview(streaming=True)
        self._answer.verticalScrollBar().setValue(
            self._answer.verticalScrollBar().maximum()
        )

    def _blink_cursor(self) -> None:
        self._cursor_visible = not self._cursor_visible
        if self._cursor_timer.isActive():
            self._render_preview(streaming=True)

    def _on_context(self, results: list) -> None:
        from app.views.result_cards import build_card
        self._clear_cards()
        self._context_results = list(results or [])
        if not results:
            self._render_preview(streaming=self._cursor_timer.isActive())
            return
        for idx, r in enumerate(results):
            card = build_card(r, parent=self._cards_widget)
            if card:
                if hasattr(card, "set_best_match"):
                    card.set_best_match(idx == 0)
                if hasattr(card, "clicked"):
                    card.clicked.connect(lambda _checked=False, c=card, result=r: self._select_result_card(c, result))
                self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
                self._result_cards.append(card)
                if idx == 0:
                    self._select_result_card(card, r)
        self._update_body_visibility()

    def _on_timings(self, timings: dict) -> None:
        stage = str(timings.get("stage", "retrieval"))
        retrieval_total = float(timings.get("retrieval_total_ms", 0.0))
        embed_ms = float(timings.get("embed_ms", 0.0))
        vector_ms = float(timings.get("vector_ms", 0.0))
        hits = int(timings.get("hits", 0))
        if stage == "complete":
            llm_load_ms = float(timings.get("llm_load_ms", 0.0))
            generation_ms = float(timings.get("generation_ms", 0.0))
            total_ms = float(timings.get("total_ms", 0.0))
            self._timing_label.setText(
                f"\u68c0\u7d22 {retrieval_total:.1f} ms  "
                f"(\u7f16\u7801 {embed_ms:.1f} + \u5411\u91cf {vector_ms:.1f})  "
                f"\u6a21\u578b {llm_load_ms:.1f} ms  "
                f"\u751f\u6210 {generation_ms:.1f} ms  "
                f"\u603b\u8017\u65f6 {total_ms:.1f} ms"
            )
        else:
            self._timing_label.setText(
                f"\u68c0\u7d22 {retrieval_total:.1f} ms  "
                f"(\u7f16\u7801 {embed_ms:.1f} + \u5411\u91cf {vector_ms:.1f})  "
                f"\u547d\u4e2d {hits} \u6761"
            )
        self._timing_label.show()

    def _on_finished(self, success: bool) -> None:
        self._cursor_timer.stop()
        self._render_preview(streaming=False)
        self._answer.verticalScrollBar().setValue(0)
        self._stop_btn.hide()

    def _on_error(self, msg: str) -> None:
        self._cursor_timer.stop()
        self._answer_plain = f"[错误] {msg}"
        self._render_preview(streaming=False)
        self._stop_btn.hide()
        self._timing_label.setText("\u68c0\u7d22\u5931\u8d25")
        self._timing_label.show()

    def _clear_cards(self) -> None:
        self._selected_card = None
        self._selected_result = None
        for card in self._result_cards:
            card.deleteLater()
        self._result_cards = []
        self._cards_label.setText("搜索结果")
        self._sync_selected_result_ui()
        self._update_body_visibility()

    def _select_result_card(self, card: QWidget, result: dict) -> None:
        if self._selected_card is card and self._selected_result == result:
            return
        if self._selected_card and hasattr(self._selected_card, "set_selected"):
            self._selected_card.set_selected(False)
        self._selected_card = card
        self._selected_result = result
        if hasattr(card, "set_selected"):
            card.set_selected(True)
        self._update_results_header()
        self._scroll_card_into_view(card)
        self._sync_selected_result_ui()
        self._render_preview(streaming=self._cursor_timer.isActive())

    def _update_results_header(self) -> None:
        total = len(self._result_cards)
        if total <= 0:
            self._cards_label.setText("搜索结果")
            return
        self._cards_label.setText(f"搜索结果 · {total} 条")

    def _scroll_card_into_view(self, card: QWidget) -> None:
        if not card:
            return
        self._cards_scroll.ensureWidgetVisible(card, 0, 18)

    def _move_result_selection(self, step: int) -> bool:
        if not self._result_cards:
            return False
        try:
            current = self._result_cards.index(self._selected_card) if self._selected_card else 0
        except ValueError:
            current = 0
        next_index = max(0, min(len(self._result_cards) - 1, current + step))
        next_card = self._result_cards[next_index]
        next_result = self._context_results[next_index] if next_index < len(self._context_results) else None
        if next_result is None:
            return False
        self._select_result_card(next_card, next_result)
        return True

    def _render_preview(self, streaming: bool) -> None:
        self._update_body_visibility()
        if streaming:
            html = _build_preview_document(
                answer_text=self._answer_plain,
                selected_result=self._selected_result,
                query_text=self._active_query,
                show_cursor=self._cursor_visible,
            )
            self._answer.setHtml(html)
            return

        html = _build_preview_document(
            answer_text=self._answer_plain,
            selected_result=self._selected_result,
            query_text=self._active_query,
            show_cursor=False,
        )
        if html:
            self._answer.setHtml(html)
        else:
            self._answer.clear()

    def _sync_selected_result_ui(self) -> None:
        result = self._selected_result
        if not result:
            self._preview_title.setText("资料卡")
            self._preview_meta.hide()
            self._copy_ref_btn.setEnabled(False)
            self._open_source_btn.setEnabled(False)
            return

        self._preview_title.setText(_result_title(result))
        meta = _result_meta(result)
        if meta:
            self._preview_meta.setText(meta)
            self._preview_meta.show()
        else:
            self._preview_meta.hide()
        self._copy_ref_btn.setEnabled(True)
        self._open_source_btn.setEnabled(_resolve_source_path(result) is not None)

    def _copy_selected_reference(self) -> None:
        if not self._selected_result:
            return
        citation = _result_reference_text(self._selected_result)
        QApplication.clipboard().setText(citation)

    def _open_selected_source(self) -> None:
        if not self._selected_result:
            return
        path = _resolve_source_path(self._selected_result)
        if path is None:
            return
        try:
            os.startfile(str(path))
        except Exception as exc:
            logger.error(f"Open source failed: {exc}")

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        if obj is self._input and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Down and self._move_result_selection(1):
                event.accept()
                return True
            if event.key() == Qt.Key_Up and self._move_result_selection(-1):
                event.accept()
                return True

        if obj in (self, self._container, self._body):
            if event.type() == QEvent.MouseMove:
                if self._resizing:
                    self._perform_resize(event.globalPos())
                    event.accept()
                    return True
                if not self._dragging:
                    self._apply_resize_cursor(event.globalPos())
            elif event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                edges = self._resize_hit_test(event.globalPos())
                if any(edges):
                    self._resizing = True
                    self._resize_edges = edges
                    self._resize_start_geom = self.geometry()
                    self._resize_start_global = event.globalPos()
                    self._user_resized = True
                    event.accept()
                    return True
            elif event.type() == QEvent.MouseButtonRelease and self._resizing:
                self._resizing = False
                self._resize_edges = (False, False, False, False)
                self._apply_resize_cursor(event.globalPos())
                event.accept()
                return True

        if obj not in (self._container, self._drag_handle):
            return super().eventFilter(obj, event)

        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            if any(self._resize_hit_test(event.globalPos())):
                return super().eventFilter(obj, event)
            self._dragging = True
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            self._drag_handle.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return True

        if event.type() == QEvent.MouseMove and self._dragging and event.buttons() & Qt.LeftButton and not self._resizing:
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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_responsive_layout()


# ---------------------------------------------------------------------------
# HTML detection & conversion helpers
# ---------------------------------------------------------------------------

import re as _re


_MD_IMAGE_RE = _re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_MD_LINK_RE = _re.compile(r"\[(?P<label>[^\]]+)\]\((?P<href>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_CONTENT_ROOT = Path(__file__).resolve().parents[2]


def _build_content_search_paths() -> list:
    candidates = [
        _CONTENT_ROOT,
        _CONTENT_ROOT / "data",
        _CONTENT_ROOT / "data" / "exports",
        _CONTENT_ROOT / "data" / "exports" / "markdown",
        _CONTENT_ROOT / "data" / "exports" / "pdf",
        _CONTENT_ROOT / "data" / "eval",
    ]

    # Also scan PDF parser output directories (marker, docling, etc.) so that
    # relative image paths like "_page_5_Picture_3.jpeg" resolve correctly.
    pdf_exports = _CONTENT_ROOT / "data" / "exports" / "pdf"
    if pdf_exports.exists():
        try:
            for subdir in pdf_exports.iterdir():
                if subdir.is_dir():
                    candidates.append(subdir)
        except OSError:
            pass

    paths = []
    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not resolved.exists():
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        paths.append(str(resolved))
    return paths


def _contains_rich_content(text: str) -> bool:
    """Return True when the answer contains HTML or markdown rich content."""
    return bool(
        _re.search(r"<(table|img|figure|div|p|ul|ol|li|br)[^>]*>", text, _re.IGNORECASE)
        or _MD_IMAGE_RE.search(text)
        or _MD_LINK_RE.search(text)
    )


def _resolve_local_src(src: str) -> tuple:
    src = (src or "").strip()
    if not src:
        return "", False
    if _re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", src):
        return src, True

    raw_path = Path(src)
    if raw_path.is_absolute():
        return raw_path.as_uri(), raw_path.exists()

    for base in _build_content_search_paths():
        candidate = Path(base) / raw_path
        if candidate.exists():
            return candidate.resolve().as_uri(), True

    return src.replace("\\", "/"), False


def _query_terms(query_text: str) -> list[str]:
    query_text = (query_text or "").strip()
    if not query_text:
        return []
    terms = [query_text]
    terms.extend(
        token.strip()
        for token in _re.split(r"[\s,，。！？；:：/\\|()\[\]{}<>\"'`]+", query_text)
        if token.strip()
    )
    deduped = []
    seen = set()
    for term in sorted(terms, key=len, reverse=True):
        if len(term) < 2:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(term)
    return deduped


def _highlight_plain_text(text: str, query_text: str) -> str:
    terms = _query_terms(query_text)
    if not terms:
        return _html_escape(text)
    pattern = "|".join(_re.escape(term) for term in terms)
    regex = _re.compile(pattern, _re.IGNORECASE)
    parts = []
    pos = 0
    for match in regex.finditer(text):
        if match.start() > pos:
            parts.append(_html_escape(text[pos:match.start()]))
        parts.append(f'<mark>{_html_escape(match.group(0))}</mark>')
        pos = match.end()
    if pos < len(text):
        parts.append(_html_escape(text[pos:]))
    return "".join(parts)


def _render_inline_markup(text: str, query_text: str = "") -> str:
    token_re = _re.compile(
        r"!\[(?P<img_alt>[^\]]*)\]\((?P<img_src>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
        r"|"
        r"\[(?P<link_label>[^\]]+)\]\((?P<link_href>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
    )
    parts = []
    pos = 0
    for match in token_re.finditer(text):
        if match.start() > pos:
            parts.append(_highlight_plain_text(text[pos:match.start()], query_text))
        if match.group("img_src") is not None:
            alt = _html_escape(match.group("img_alt") or "image")
            raw_src = match.group("img_src") or ""
            src, exists = _resolve_local_src(raw_src)
            if exists:
                parts.append(f'<img src="{_html_escape(src)}" alt="{alt}">')
            else:
                parts.append(
                    '<span class="missing-image">'
                    f"[图片未找到: {_html_escape(raw_src)}]"
                    '</span>'
                )
        else:
            label = _highlight_plain_text(match.group("link_label") or "", query_text)
            href, _exists = _resolve_local_src(match.group("link_href") or "")
            parts.append(f'<a href="{_html_escape(href)}">{label}</a>')
        pos = match.end()
    if pos < len(text):
        parts.append(_highlight_plain_text(text[pos:], query_text))
    return "".join(parts)


def _document_style() -> str:
    return """
    <style>
      body { font-family: 'Microsoft YaHei UI', sans-serif; font-size: 14px;
             color: #16202a; line-height: 1.65; }
      mark { background: rgba(124, 211, 183, 0.28); color: #16202a;
             padding: 0 2px; border-radius: 4px; }
      img  { max-width: 100%; height: auto; border-radius: 6px;
             margin: 6px 0; display: block; }
      table { border-collapse: collapse; width: 100%; margin: 8px 0; }
      th, td { border: 1px solid rgba(15,23,32,0.12); padding: 5px 10px;
               text-align: left; }
      th { background: rgba(92,178,233,0.10); color: #1d4e89; font-weight: 600; }
      tr:nth-child(even) { background: rgba(15,23,32,0.03); }
      a { color: #1d4e89; }
      p { margin: 4px 0; }
      .missing-image { display: inline-block; padding: 4px 8px;
                       border: 1px dashed rgba(15,23,32,0.18);
                       border-radius: 6px; color: #9a5b20;
                       background: rgba(241,180,124,0.08); }
      .preview-block { margin: 0 0 18px 0; padding: 0 0 14px 0;
                       border-bottom: 1px solid rgba(15,23,32,0.08); }
      .preview-block:last-child { border-bottom: none; padding-bottom: 0; }
      .preview-label { color: #1d4e89; font-size: 12px; font-weight: 700;
                       letter-spacing: 1px; margin: 0 0 8px 0; }
      .preview-answer { background: rgba(124,211,183,0.08);
                        border: 1px solid rgba(124,211,183,0.22);
                        border-radius: 14px; padding: 12px 14px; }
      .preview-empty { color: #6a7785; font-size: 13px; margin: 8px 0; }
    </style>
    """


def _plain_to_fragment(text: str, query_text: str = "") -> str:
    text = _re.sub(r"\[鍥剧墖\]", '<span style="color:#8fa3b6;">[图片]</span>', text)

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
            buf.append(_render_inline_markup(stripped, query_text))

    flush_buf()
    return "\n".join(parts)


def _plain_to_html(text: str, query_text: str = "") -> str:
    body = _plain_to_fragment(text, query_text)
    return f"<html><head>{_document_style()}</head><body>{body}</body></html>"


def _result_title(result: Optional[dict]) -> str:
    if not result:
        return "回答"
    payload = result.get("pdf_payload") or {}
    heading_path = str(payload.get("heading_path") or "").strip()
    if heading_path:
        return heading_path.split(" > ")[-1][:80]
    document = str(payload.get("display_content") or result.get("document") or result.get("content") or "").strip()
    for line in document.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:80]
    source_type = str(result.get("source_type") or "").lower()
    if source_type == "video":
        payload = result.get("video_payload") or {}
        return str(payload.get("file") or "视频片段").strip() or "视频片段"
    payload = result.get("pdf_payload") or {}
    file_name = str(payload.get("file") or "").strip()
    if file_name:
        return Path(file_name).stem
    return "知识卡"


def _result_meta(result: Optional[dict]) -> str:
    if not result:
        return ""
    source_type = str(result.get("source_type") or "").lower() or "doc"
    badge = source_type.upper()
    payload = result.get("pdf_payload") or {}
    file_name = str(payload.get("file") or "").strip()
    page = payload.get("page", "")
    block_type = str(payload.get("block_type") or "").strip()
    parts = [badge]
    if file_name:
        parts.append(Path(file_name).name)
    if page not in ("", None):
        parts.append(f"第 {page} 页")
    if block_type:
        parts.append(block_type)
    return "  ·  ".join(parts)


def _result_reference_text(result: dict) -> str:
    anchor = str(result.get("anchor_id") or result.get("id") or "").strip()
    title = _result_title(result)
    meta = _result_meta(result)
    chunks = [title]
    if meta:
        chunks.append(meta)
    if anchor:
        chunks.append(f"anchor_id={anchor}")
    return " | ".join(chunks)


def _resolve_source_path(result: Optional[dict]) -> Optional[Path]:
    """Resolve the best viewable file path for a search result.

    Priority order:
    1. The exported Markdown file in data/exports/markdown/<stem>.chunks.md
       — this is the human-editable L1 file that mirrors the original document
       and is the most useful for quick reference.
    2. The exported Markdown in data/exports/markdown/<stem>.md
    3. The original source file (PDF / video / raw MD) stored in data/
    """
    if not result:
        return None

    source_type = str(result.get("source_type") or "").lower()

    # ── Video ────────────────────────────────────────────────
    if source_type == "video":
        payload = result.get("video_payload") or {}
        file_name = str(payload.get("file") or "").strip()
        if not file_name:
            return None
        path = cfg.abs_path(f"data/{file_name}")
        if path.exists():
            return path
        raw = Path(file_name)
        return raw if raw.exists() else None

    # ── PDF / Markdown / generic document ────────────────────
    payload = result.get("pdf_payload") or {}
    file_name = str(payload.get("file") or "").strip()
    if not file_name:
        return None

    stem = Path(file_name).stem

    # 1. Exported chunks Markdown (L1 — human-readable)
    md_exports = cfg.abs_path("data/exports/markdown")
    chunks_md = md_exports / f"{stem}.chunks.md"
    if chunks_md.exists():
        return chunks_md

    # 2. Plain exported Markdown (from PDF parser output directory)
    plain_md = md_exports / f"{stem}.md"
    if plain_md.exists():
        return plain_md

    # 3. PDF parser output directory contains an .md file
    pdf_exports = cfg.abs_path("data/exports/pdf")
    for subdir in pdf_exports.iterdir() if pdf_exports.exists() else []:
        if subdir.is_dir() and stem in subdir.name:
            for candidate in subdir.glob("*.md"):
                return candidate

    # 4. Original source file
    for candidate in [
        cfg.abs_path(f"data/{file_name}"),
        cfg.abs_path(file_name),
        Path(file_name),
    ]:
        if candidate.exists():
            return candidate

    return None


def _build_preview_document(answer_text: str, selected_result: Optional[dict], query_text: str, show_cursor: bool) -> str:
    sections = []
    answer_text = (answer_text or "").strip()
    if answer_text:
        answer_fragment = _plain_to_fragment(answer_text + ("▋" if show_cursor else ""), query_text)
        sections.append(
            '<section class="preview-block preview-answer">'
            '<div class="preview-label">回答</div>'
            f'{answer_fragment}'
            '</section>'
        )
    else:
        sections.append('<p class="preview-empty">正在等待回答生成…</p>' if show_cursor else '<p class="preview-empty">输入问题后，这里会在右侧显示回答。</p>')

    if selected_result:
        payload = selected_result.get("pdf_payload") or {}
        content = str(payload.get("display_content") or selected_result.get("document") or selected_result.get("content") or "").strip()
        metadata = payload.get("metadata") or {}
        semantic_description = str(metadata.get("semantic_description") or "").strip() if isinstance(metadata, dict) else ""
        heading_path = str(payload.get("heading_path") or "").strip()
        block_type = str(payload.get("block_type") or "").strip()

        if content:
            body_parts = []

            # ── Heading breadcrumb ────────────────────────────────
            if heading_path:
                # Render each path segment as a breadcrumb chain
                segments = heading_path.split(" > ")
                crumb_html = ' <span style="color:#8fa3b6;font-size:11px;">›</span> '.join(
                    f'<span style="color:#5cb2e9;">{_html_escape(s)}</span>' for s in segments
                )
                body_parts.append(
                    f'<p style="font-size:11px;color:#7890a3;margin:0 0 6px 0;">{crumb_html}</p>'
                )

            # ── Semantic description ──────────────────────────────
            if semantic_description:
                body_parts.append(
                    f'<p style="color:#516174;font-size:12px;font-style:italic;margin:0 0 8px 0;">'
                    f'{_html_escape(semantic_description)}</p>'
                )

            # ── Main content (with image rendering) ──────────────
            body_parts.append(_plain_to_fragment(content, query_text))

            # ── Source file link ──────────────────────────────────
            file_name = str(payload.get("file") or "").strip()
            page = payload.get("page", "")
            source_path = _resolve_source_path(selected_result)
            if source_path is not None:
                uri = source_path.resolve().as_uri()
                anchor = ""
                if heading_path:
                    # Generate a markdown anchor from the last heading segment
                    last = heading_path.split(" > ")[-1]
                    anchor = "#" + _re.sub(r"[^\w\u4e00-\u9fff-]", "", last.lower().replace(" ", "-"))
                link_label = source_path.name
                if page not in ("", None):
                    link_label += f" · 第 {page} 页"
                body_parts.append(
                    f'<p style="margin:10px 0 0 0;font-size:11px;">'
                    f'<a href="{_html_escape(uri + anchor)}" style="color:#5cb2e9;">📄 在文档中查看：{_html_escape(link_label)}</a>'
                    f'</p>'
                )

            full_body = "\n".join(body_parts)
            sections.append(
                '<section class="preview-block">'
                '<div class="preview-label">命中内容</div>'
                f'{full_body}'
                '</section>'
            )

    body = "\n".join(sections)
    return f"<html><head>{_document_style()}</head><body>{body}</body></html>"

