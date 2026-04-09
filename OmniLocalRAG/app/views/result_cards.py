"""
result_cards.py — factory + widget classes for PDF and Video result cards.
"""
import os
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.utils import config as cfg
from app.utils.logger import logger

_THUMB_DIR = cfg.abs_path("data/thumbs")


def build_card(result: dict, parent: Optional[QWidget] = None) -> Optional[QWidget]:
    """Factory: returns the correct card widget based on source_type."""
    source_type = result.get("source_type", "")
    if source_type == "pdf":
        return PDFCard(result, parent)
    if source_type == "video":
        return VideoCard(result, parent)
    if source_type == "hybrid":
        return HybridCard(result, parent)
    return None


# ---------------------------------------------------------------------------
# PDFCard
# ---------------------------------------------------------------------------

class PDFCard(QFrame):
    def __init__(self, data: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("PDFCard")
        self.setFrameShape(QFrame.StyledPanel)
        self._data = data
        self._build()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # Thumbnail (lazy: show placeholder, load on show)
        self._thumb = QLabel()
        self._thumb.setFixedSize(120, 90)
        self._thumb.setObjectName("CardThumb")
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setText("PDF")
        layout.addWidget(self._thumb)

        # Text info
        info = QVBoxLayout()
        pdf_payload = self._data.get("pdf_payload") or {}
        file_name = pdf_payload.get("file", "")
        page = pdf_payload.get("page", "")
        content = self._data.get("document", "")[:200]

        title = QLabel(f"{file_name}  第 {page} 页")
        title.setObjectName("CardTitle")
        title.setWordWrap(False)

        body = QLabel(content)
        body.setObjectName("CardBody")
        body.setWordWrap(True)
        body.setMaximumHeight(60)

        info.addWidget(title)
        info.addWidget(body)
        info.addStretch()
        layout.addLayout(info)

    def mouseDoubleClickEvent(self, event) -> None:
        pdf_payload = self._data.get("pdf_payload") or {}
        file_name = pdf_payload.get("file", "")
        page = pdf_payload.get("page", 1)
        file_path = cfg.abs_path(f"data/{file_name}")
        if not file_path.exists():
            # Try absolute path stored in payload
            file_path = Path(file_name)
        try:
            url = f"file:///{file_path}#page={page}"
            import subprocess
            os.startfile(str(file_path))
        except Exception as e:
            logger.error(f"Open PDF failed: {e}")
        super().mouseDoubleClickEvent(event)


# ---------------------------------------------------------------------------
# VideoCard
# ---------------------------------------------------------------------------

class VideoCard(QFrame):
    def __init__(self, data: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoCard")
        self.setFrameShape(QFrame.StyledPanel)
        self._data = data
        self._player_widget = None
        self._build()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # Thumbnail / play button
        self._thumb = QPushButton()
        self._thumb.setObjectName("VideoThumb")
        self._thumb.setFixedSize(120, 90)
        self._thumb.setText("▶")
        self._thumb.clicked.connect(self._open_player)

        video_payload = self._data.get("video_payload") or {}
        clip_id = self._data.get("id", "")
        thumb_path = _THUMB_DIR / f"{clip_id}.jpg"
        if thumb_path.exists():
            px = QPixmap(str(thumb_path)).scaled(120, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._thumb.setIcon(px if not px.isNull() else QPixmap())
            self._thumb.setIconSize(self._thumb.size())

        layout.addWidget(self._thumb)

        # Info
        info = QVBoxLayout()
        start = video_payload.get("start", 0)
        end = video_payload.get("end", 0)
        file_name = video_payload.get("file", "")
        summary = self._data.get("document", "")[:200]

        ts = f"{_fmt_time(start)} - {_fmt_time(end)}"
        title = QLabel(f"{file_name}  [{ts}]")
        title.setObjectName("CardTitle")

        body = QLabel(summary)
        body.setObjectName("CardBody")
        body.setWordWrap(True)
        body.setMaximumHeight(60)

        info.addWidget(title)
        info.addWidget(body)
        info.addStretch()
        layout.addLayout(info)

    def _open_player(self) -> None:
        video_payload = self._data.get("video_payload") or {}
        file_name = video_payload.get("file", "")
        start_sec = video_payload.get("start", 0)

        file_path = cfg.abs_path(f"data/{file_name}")
        if not file_path.exists():
            file_path = Path(file_name)
        if not file_path.exists():
            logger.warning(f"Video file not found: {file_path}")
            return

        from app.views.video_player import VideoPlayerDialog
        dlg = VideoPlayerDialog(str(file_path), int(start_sec * 1000), self)
        dlg.show()


# ---------------------------------------------------------------------------
# HybridCard
# ---------------------------------------------------------------------------

class HybridCard(QFrame):
    def __init__(self, data: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("HybridCard")
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addWidget(PDFCard(data, self))
        layout.addWidget(VideoCard(data, self))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"
