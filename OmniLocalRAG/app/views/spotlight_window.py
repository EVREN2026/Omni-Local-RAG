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
    QSizePolicy,
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
        self._context_results: list = []
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
        self._timing_label = optional_child(self, QLabel, "timingLabel")
        self._knowledge_card = require_child(self, QFrame, "knowledgeCardFrame", "Spotlight UI")
        self._preview_title = require_child(self, QLabel, "previewTitleLabel", "Spotlight UI")
        self._preview_meta = optional_child(self, QLabel, "previewMetaLabel")
        answer_host = optional_child(self, QWidget, "answerHostWidget")

        # Keep legacy object names so the existing QSS keeps applying after .ui loading.
        self._container.setObjectName("SpotlightContainer")
        self._drag_handle.setObjectName("SpotlightDragHandle")
        self._input.setObjectName("SearchInput")
        self._stop_btn.setObjectName("StopButton")

        # ── Heartbeat bar: pulse indicator + timing label ──
        self._heartbeat_bar = QWidget(self._container)
        self._heartbeat_bar.setObjectName("HeartbeatBar")
        hb_layout = QHBoxLayout(self._heartbeat_bar)
        hb_layout.setContentsMargins(8, 0, 8, 2)
        hb_layout.setSpacing(6)

        self._pulse_label = QLabel(self._heartbeat_bar)
        self._pulse_label.setObjectName("PulseIndicator")
        self._pulse_label.setFixedSize(10, 10)
        self._pulse_label.hide()

        self._timing_label = QLabel("", self._heartbeat_bar)
        self._timing_label.setObjectName("SearchTimingLabel")

        hb_layout.addWidget(self._pulse_label)
        hb_layout.addWidget(self._timing_label, 1)
        self._heartbeat_bar.hide()

        # Insert heartbeat bar into container layout (replacing old timing label)
        container_layout = self._container.layout()
        if isinstance(container_layout, QVBoxLayout):
            # Remove old timing label if it was in the layout
            if self._timing_label is not None:
                old_timing = optional_child(self, QLabel, "timingLabel")
                if old_timing and old_timing.parent() == self._container:
                    container_layout.removeWidget(old_timing)
                    old_timing.setParent(None)
            container_layout.insertWidget(2, self._heartbeat_bar)

        # Pulse animation: cycle through dots to show activity
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(400)
        self._pulse_step = 0
        self._pulse_timer.timeout.connect(self._tick_pulse)

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

        self._drag_handle.setCursor(Qt.OpenHandCursor)
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
        self._answer.anchorClicked.connect(self._on_anchor_clicked)
        # Set base font so zoomIn/zoomOut has a reference point
        from PyQt5.QtGui import QFont
        base_font = QFont()
        base_font.setPointSize(12)
        self._answer.setFont(base_font)
        answer_layout = answer_host.layout()
        answer_layout.addWidget(self._answer)
        self._answer_plain: str = ""

        self._sync_window_size(force=True)
        self.setMouseTracking(True)
        self._container.setMouseTracking(True)
        self.installEventFilter(self)
        self._container.installEventFilter(self)
        self._drag_handle.installEventFilter(self)
        self._input.installEventFilter(self)
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
        self._knowledge_card.show()

    def _apply_responsive_layout(self, force: bool = False) -> None:
        pass

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
        self._answer.clear()
        self._render_preview(streaming=False)
        self._timing_label.setText("检索中")
        self._pulse_label.show()
        self._pulse_step = 0
        self._pulse_timer.start()
        self._heartbeat_bar.show()
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
        self._context_results = list(results or [])
        self._sync_result_ui()
        self._render_preview(streaming=self._cursor_timer.isActive())

    def _on_timings(self, timings: dict) -> None:
        stage = str(timings.get("stage", "retrieval"))
        route_ms = float(timings.get("route_ms", 0.0))
        locate_ms = float(timings.get("locate_ms", 0.0))
        validate_ms = float(timings.get("validate_ms", 0.0))
        category = str(timings.get("category", "general"))
        hits = int(timings.get("hits", 0))
        validated = timings.get("validated", False)

        # Compact timing: route→locate→validate with ms
        parts = [f"路由 {route_ms:.0f}ms", f"定位 {locate_ms:.0f}ms", f"验证 {validate_ms:.0f}ms"]

        if stage == "complete":
            generation_ms = float(timings.get("generation_ms", 0.0))
            total_ms = float(timings.get("total_ms", 0.0))
            parts.append(f"生成 {generation_ms:.0f}ms")
            parts.append(f"总耗时 {total_ms:.0f}ms")

        # Category + hits summary
        check = "✓" if validated else "✗"
        parts.append(f"{category} · {hits}条{check}")

        self._timing_label.setText(" → ".join(parts))
        self._heartbeat_bar.show()

        # Stop pulse when complete
        if stage == "complete":
            self._pulse_timer.stop()
            self._pulse_label.setText("●")
            self._pulse_label.setStyleSheet("color: #4caf50; font-size: 10px;")

    def _tick_pulse(self) -> None:
        """Animate the pulse indicator dot during search."""
        self._pulse_step = (self._pulse_step + 1) % 4
        dots = ["●", "◉", "●", "○"]
        self._pulse_label.setText(dots[self._pulse_step])
        # Alternate between bright and dim
        if self._pulse_step % 2 == 0:
            self._pulse_label.setStyleSheet("color: #4fc3f7; font-size: 10px;")
        else:
            self._pulse_label.setStyleSheet("color: #81d4fa; font-size: 10px;")

    def _on_finished(self, success: bool) -> None:
        self._cursor_timer.stop()
        self._pulse_timer.stop()
        self._pulse_label.setText("●")
        self._pulse_label.setStyleSheet("color: #4caf50; font-size: 10px;")
        self._render_preview(streaming=False)
        self._answer.verticalScrollBar().setValue(0)
        self._stop_btn.hide()

    def _on_error(self, msg: str) -> None:
        self._cursor_timer.stop()
        self._pulse_timer.stop()
        self._pulse_label.setText("●")
        self._pulse_label.setStyleSheet("color: #f44336; font-size: 10px;")
        self._answer_plain = f"[错误] {msg}"
        self._render_preview(streaming=False)
        self._stop_btn.hide()
        self._timing_label.setText("检索失败")
        self._heartbeat_bar.show()

    def _render_preview(self, streaming: bool) -> None:
        self._update_body_visibility()
        if streaming:
            html = _build_preview_document(
                answer_text=self._answer_plain,
                query_text=self._active_query,
                show_cursor=self._cursor_visible,
                context_results=self._context_results,
            )
            self._answer.setHtml(html)
            return

        html = _build_preview_document(
            answer_text=self._answer_plain,
            query_text=self._active_query,
            show_cursor=False,
            context_results=self._context_results,
        )
        if html:
            self._answer.setHtml(html)
        else:
            self._answer.clear()

    def _sync_result_ui(self) -> None:
        """Update title/meta/buttons based on context results."""
        if not self._context_results:
            self._preview_title.setText("资料卡")
            self._preview_meta.hide()
            return

        first = self._context_results[0]
        hits = len(self._context_results)
        self._preview_title.setText(f"搜索结果 · {hits} 条命中")
        meta = _result_meta(first)
        if meta:
            self._preview_meta.setText(meta)
            self._preview_meta.show()
        else:
            self._preview_meta.hide()

    def _on_anchor_clicked(self, url) -> None:
        """Handle clicks on anchor:// and video:// citation links."""
        url_str = str(url.toEncoded(), "utf-8") if hasattr(url, "toEncoded") else str(url)

        if url_str.startswith("video://"):
            clip_id = url_str[len("video://"):]
            for result in self._context_results:
                result_id = str(result.get("id") or "").strip()
                if result_id == clip_id:
                    path = _resolve_source_path(result)
                    if path is not None:
                        try:
                            os.startfile(str(path))
                        except Exception as exc:
                            logger.error(f"Open video failed: {exc}")
                    return
            logger.debug(f"No video result found for clip_id: {clip_id}")

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        if obj in (self, self._container):
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
        # Only hide when focus leaves the application entirely.
        # If focus moves to a child dialog (e.g. PreferencesDialog) or another
        # widget within the same app, keep the spotlight visible.
        reason = event.reason()
        if reason in (QEvent.Mouse, QEvent.TabFocusReason, QEvent.BacktabFocusReason):
            # User clicked outside or tabbed away — hide
            self.hide_window()
        # For ActiveWindowFocusReason / PopupFocusReason / WindowFocusReason,
        # another window in the same app gained focus — keep visible
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
_CITATION_RE = _re.compile(r"\[(\d+)\]")
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
      body { font-family: 'Microsoft YaHei UI', sans-serif;
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
      .preview-label { color: #1d4e89; font-size: 0.85em; font-weight: 700;
                       letter-spacing: 1px; margin: 0 0 8px 0; }
      .preview-answer { background: rgba(124,211,183,0.08);
                        border: 1px solid rgba(124,211,183,0.22);
                        border-radius: 14px; padding: 12px 14px; }
      .preview-empty { color: #6a7785; font-size: 0.92em; margin: 8px 0; }
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


def _link_citations(text: str, context_results: list) -> str:
    """Replace [1], [2] etc. with clickable links to anchor_id/video_clip_id."""
    if not context_results:
        return text

    def _replace(match):
        idx = int(match.group(1)) - 1  # 0-based
        if 0 <= idx < len(context_results):
            result = context_results[idx]
            anchor = result.get("anchor_id") or result.get("id") or ""
            source_type = str(result.get("source_type", "")).lower()
            if source_type == "video":
                clip_id = result.get("id", "")
                return f'<a href="video://{_html_escape(clip_id)}" style="color:#1d4e89;text-decoration:none;font-weight:600;">[{idx+1}]</a>'
            if anchor:
                return f'<a href="anchor://{_html_escape(anchor)}" style="color:#1d4e89;text-decoration:none;font-weight:600;">[{idx+1}]</a>'
        return match.group(0)

    return _CITATION_RE.sub(_replace, text)


def _build_preview_document(answer_text: str, query_text: str, show_cursor: bool, context_results: list = None) -> str:
    sections = []
    answer_text = (answer_text or "").strip()
    if answer_text:
        answer_fragment = _plain_to_fragment(answer_text + ("▋" if show_cursor else ""), query_text)
        if context_results:
            answer_fragment = _link_citations(answer_fragment, context_results)
        sections.append(
            '<section class="preview-block preview-answer">'
            '<div class="preview-label">回答</div>'
            f'{answer_fragment}'
            '</section>'
        )
    else:
        sections.append('<p class="preview-empty">正在等待回答生成…</p>' if show_cursor else '<p class="preview-empty">输入问题后，这里会显示回答。</p>')

    if context_results:
        for i, result in enumerate(context_results, 1):
            payload = result.get("pdf_payload") or {}
            content = str(payload.get("display_content") or result.get("document") or result.get("content") or "").strip()
            metadata = payload.get("metadata") or {}
            semantic_description = str(metadata.get("semantic_description") or "").strip() if isinstance(metadata, dict) else ""
            heading_path = str(payload.get("heading_path") or "").strip()
            block_type = str(payload.get("block_type") or "").strip()

            if not content:
                continue

            body_parts = []

            # ── Heading breadcrumb ────────────────────────────────
            if heading_path:
                segments = heading_path.split(" > ")
                crumb_html = ' <span style="color:#8fa3b6;font-size:0.78em;">›</span> '.join(
                    f'<span style="color:#5cb2e9;">{_html_escape(s)}</span>' for s in segments
                )
                body_parts.append(
                    f'<p style="font-size:0.78em;color:#7890a3;margin:0 0 6px 0;">{crumb_html}</p>'
                )

            # ── Semantic description ──────────────────────────────
            if semantic_description:
                body_parts.append(
                    f'<p style="color:#516174;font-size:0.85em;font-style:italic;margin:0 0 8px 0;">'
                    f'{_html_escape(semantic_description)}</p>'
                )

            # ── Main content (with image rendering) ──────────────
            body_parts.append(_plain_to_fragment(content, query_text))

            # ── Source trace (anchor_id / video_clip_id) ──────────
            anchor_id = str(result.get("anchor_id") or "").strip()
            result_id = str(result.get("id") or "").strip()
            result_source_type = str(result.get("source_type") or "").lower()
            trace_parts = []
            if anchor_id:
                trace_parts.append(
                    f'<span style="color:#7890a3;">anchor_id:</span> '
                    f'<a href="anchor://{_html_escape(anchor_id)}" style="color:#5cb2e9;">{_html_escape(anchor_id[:32])}</a>'
                )
            if result_source_type == "video" and result_id:
                trace_parts.append(
                    f'<span style="color:#7890a3;">video_clip_id:</span> '
                    f'<a href="video://{_html_escape(result_id)}" style="color:#5cb2e9;">{_html_escape(result_id[:32])}</a>'
                )
            if trace_parts:
                body_parts.append(
                    f'<p style="margin:8px 0 0 0;font-size:0.72em;">'
                    + ' &nbsp;|&nbsp; '.join(trace_parts)
                    + '</p>'
                )

            full_body = "\n".join(body_parts)
            sections.append(
                '<section class="preview-block">'
                f'{full_body}'
                '</section>'
            )

    body = "\n".join(sections)
    return f"<html><head>{_document_style()}</head><body>{body}</body></html>"

