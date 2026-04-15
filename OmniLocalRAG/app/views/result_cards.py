from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QFrame, QLabel, QSizePolicy, QWidget

from app.utils.ui_loader import load_ui, require_child


def build_card(result: dict, parent: Optional[QWidget] = None) -> Optional[QWidget]:
    return SearchResultCard(result, parent)


def _payload(result: dict) -> dict:
    payload = result.get("pdf_payload") or {}
    return payload if isinstance(payload, dict) else {}


class SearchResultCard(QFrame):
    clicked = pyqtSignal()

    def __init__(self, data: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._data = data
        self.setObjectName("ResultListCard")
        self.setProperty("selected", False)
        self.setProperty("bestMatch", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._build()

    def _build(self) -> None:
        load_ui(self, "search_result_card.ui")
        self._title = require_child(self, QLabel, "titleLabel", "SearchResultCard UI")
        self._tag = require_child(self, QLabel, "sourceTagLabel", "SearchResultCard UI")
        self._meta = require_child(self, QLabel, "metaLabel", "SearchResultCard UI")
        self._summary = require_child(self, QLabel, "summaryLabel", "SearchResultCard UI")

        self._title.setObjectName("ResultListTitle")
        self._tag.setObjectName("ResultSourceTag")
        self._meta.setObjectName("ResultMeta")
        self._summary.setObjectName("ResultSummary")

        self._title.setText(_result_title(self._data))
        self._tag.setText(_result_source_tag(self._data))
        meta_text = _result_meta(self._data)
        self._meta.setText(meta_text)
        self._meta.setVisible(bool(meta_text))

        # Breadcrumb: show full heading_path as tooltip and truncated label
        breadcrumb = _result_breadcrumb(self._data)
        if breadcrumb:
            self._title.setToolTip(breadcrumb)
            self._meta.setToolTip(breadcrumb)

        self._summary.setText(_result_summary(self._data))

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_best_match(self, best_match: bool) -> None:
        self.setProperty("bestMatch", best_match)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


def _display_content(result: dict) -> str:
    payload = _payload(result)
    return str(payload.get("display_content") or result.get("document") or result.get("content") or "").strip()


def _result_breadcrumb(result: dict) -> str:
    """Return the full heading_path as a human-readable breadcrumb string."""
    payload = _payload(result)
    heading_path = str(payload.get("heading_path") or "").strip()
    return heading_path


def _result_title(result: dict) -> str:
    payload = _payload(result)
    heading_path = str(payload.get("heading_path") or "").strip()
    if heading_path:
        # Show only the last (most specific) segment — full path visible as tooltip
        return heading_path.split(" > ")[-1][:72]

    document = _display_content(result)
    for line in document.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:72]

    file_name = str(payload.get("file") or "").strip()
    if file_name:
        return Path(file_name).stem

    video_payload = result.get("video_payload") or {}
    video_file = str(video_payload.get("file") or "").strip()
    if video_file:
        return Path(video_file).stem

    return "知识片段"


def _result_summary(result: dict) -> str:
    payload = _payload(result)
    metadata = payload.get("metadata") or {}
    semantic_description = ""
    if isinstance(metadata, dict):
        semantic_description = str(metadata.get("semantic_description") or "").strip()
    if semantic_description:
        summary = semantic_description
    else:
        document = _display_content(result)
        lines = []
        for line in document.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                lines.append(stripped)
        summary = " ".join(lines[1:]) if len(lines) > 1 else (lines[0] if lines else "")
    summary = summary.replace("  ", " ").strip()
    if len(summary) > 110:
        return summary[:110].rstrip() + "..."
    return summary or "暂无摘要"


def _result_source_tag(result: dict) -> str:
    source_type = str(result.get("source_type") or "").lower()
    if source_type == "video":
        return "VIDEO"
    if source_type == "markdown":
        return "MD"
    if source_type == "pdf":
        return "PDF"
    return "DOC"


def _result_meta(result: dict) -> str:
    """Return file · page · block_type meta string, plus heading breadcrumb."""
    source_type = str(result.get("source_type") or "").lower()
    if source_type == "video":
        payload = result.get("video_payload") or {}
        file_name = str(payload.get("file") or "").strip()
        start = float(payload.get("start", 0) or 0)
        end = float(payload.get("end", 0) or 0)
        if file_name:
            return f"{Path(file_name).name} · {_fmt_time(start)}-{_fmt_time(end)}"
        return f"{_fmt_time(start)}-{_fmt_time(end)}"

    payload = _payload(result)
    file_name = str(payload.get("file") or "").strip()
    page = payload.get("page", "")
    block_type = str(payload.get("block_type") or "").strip()
    heading_path = str(payload.get("heading_path") or "").strip()

    parts = []
    if file_name:
        parts.append(Path(file_name).name)
    if page not in ("", None):
        parts.append(f"第 {page} 页")
    if block_type:
        parts.append(block_type)

    # Show parent heading segment (one level above the last) as location context
    if heading_path and " > " in heading_path:
        segments = heading_path.split(" > ")
        if len(segments) >= 2:
            parent = segments[-2][:40]
            parts.append(f"§ {parent}")

    return " · ".join(parts)


def _fmt_time(seconds: float) -> str:
    value = int(seconds)
    return f"{value // 60:02d}:{value % 60:02d}"

