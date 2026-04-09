"""
VideoWorkbench — timeline-based video clip annotation tool.
ASR segments are displayed as colour-coded blocks on a timeline.
User presses I (in-point) and O (out-point) to mark a clip, then
Enter to confirm and enter a semantic summary.
"""
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.controllers.ingest_controller import IngestController
from app.models.sqlite_store import SQLiteStore
from app.utils import config as cfg
from app.utils.logger import logger


class VideoWorkbench(QWidget):
    clip_created = pyqtSignal(dict)  # emitted after a clip is saved

    def __init__(
        self,
        ingest_ctrl: IngestController,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ingest = ingest_ctrl
        self._video_path: Optional[str] = None
        self._duration_sec: float = 0.0
        self._segments: List[dict] = []
        self._mark_in: Optional[float] = None
        self._mark_out: Optional[float] = None
        self._build()
        self._connect_signals()

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        # Status / progress
        self._status = QLabel("未加载视频")
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.hide()
        layout.addWidget(self._status)
        layout.addWidget(self._progress)

        # Timeline
        self._timeline = _TimelineWidget()
        self._timeline.setMinimumHeight(80)
        self._timeline.position_clicked.connect(self._seek_to)
        layout.addWidget(self._timeline)

        # Playback controls
        ctrl_row = QHBoxLayout()
        self._play_btn = QPushButton("▶ 播放")
        self._play_btn.clicked.connect(self._toggle_play)
        self._pos_label = QLabel("00:00")

        btn_mark_in = QPushButton("[I] 标记开始")
        btn_mark_in.clicked.connect(self._mark_start)
        btn_mark_out = QPushButton("[O] 标记结束")
        btn_mark_out.clicked.connect(self._mark_end)
        btn_confirm = QPushButton("↵ 确认切片")
        btn_confirm.clicked.connect(self._confirm_clip)

        for w in (self._play_btn, self._pos_label, btn_mark_in, btn_mark_out, btn_confirm):
            ctrl_row.addWidget(w)
        ctrl_row.addStretch()
        layout.addLayout(ctrl_row)

        # Mark labels
        self._mark_label = QLabel("In: --  Out: --")
        layout.addWidget(self._mark_label)

        # Clip list
        self._clip_list_label = QLabel("已标记片段：")
        layout.addWidget(self._clip_list_label)

        self.setFocusPolicy(Qt.StrongFocus)

        # QMediaPlayer for preview
        self._player = QMediaPlayer(self)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)

    def _connect_signals(self) -> None:
        self._ingest.asr_segment_ready.connect(self._on_asr_segment)
        self._ingest.asr_progress.connect(self._on_asr_progress)
        self._ingest.asr_finished.connect(self._on_asr_finished)

    def load_video(self, video_path: str) -> None:
        self._video_path = video_path
        self._segments = []
        self._mark_in = None
        self._mark_out = None
        self._status.setText(f"已加载: {Path(video_path).name}  (ASR 转写中…)")
        self._progress.setValue(0)
        self._progress.show()
        self._player.setMedia(QMediaContent(
            __import__("PyQt5.QtCore", fromlist=["QUrl"]).QUrl.fromLocalFile(video_path)
        ))

    # ------------------------------------------------------------------
    # ASR signal handlers
    # ------------------------------------------------------------------

    def _on_asr_segment(self, seg: dict) -> None:
        self._segments.append(seg)
        self._timeline.add_segment(seg, self._duration_sec)

    def _on_asr_progress(self, ratio: float) -> None:
        self._progress.setValue(int(ratio * 100))

    def _on_asr_finished(self, success: bool) -> None:
        self._progress.hide()
        self._status.setText(
            f"转写完成，共 {len(self._segments)} 段" if success else "转写失败"
        )

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _toggle_play(self) -> None:
        if self._player.state() == QMediaPlayer.PlayingState:
            self._player.pause()
            self._play_btn.setText("▶ 播放")
        else:
            self._player.play()
            self._play_btn.setText("⏸ 暂停")

    def _seek_to(self, sec: float) -> None:
        self._player.setPosition(int(sec * 1000))

    def _on_position_changed(self, ms: int) -> None:
        sec = ms / 1000
        self._pos_label.setText(_fmt(sec))
        self._timeline.set_playhead(sec)

    def _on_duration_changed(self, ms: int) -> None:
        self._duration_sec = ms / 1000
        self._timeline.set_duration(self._duration_sec)

    # ------------------------------------------------------------------
    # Clip marking
    # ------------------------------------------------------------------

    def _mark_start(self) -> None:
        self._mark_in = self._player.position() / 1000
        self._update_mark_label()

    def _mark_end(self) -> None:
        self._mark_out = self._player.position() / 1000
        self._update_mark_label()

    def _update_mark_label(self) -> None:
        i = _fmt(self._mark_in) if self._mark_in is not None else "--"
        o = _fmt(self._mark_out) if self._mark_out is not None else "--"
        self._mark_label.setText(f"In: {i}  Out: {o}")

    def _confirm_clip(self) -> None:
        if self._mark_in is None or self._mark_out is None:
            QMessageBox.warning(self, "提示", "请先标记开始(I)和结束(O)点")
            return
        if self._mark_in >= self._mark_out:
            QMessageBox.warning(self, "提示", "结束时间必须大于开始时间")
            return
        summary, ok = QInputDialog.getText(
            self, "语义摘要", f"请输入 {_fmt(self._mark_in)}–{_fmt(self._mark_out)} 的语义摘要："
        )
        if not ok or not summary.strip():
            return

        db = SQLiteStore()
        clip_id = db.insert_clip(
            video_file=Path(self._video_path).name if self._video_path else "",
            start_sec=self._mark_in,
            end_sec=self._mark_out,
            semantic_summary=summary.strip(),
        )
        logger.info(f"Clip created: {clip_id} [{_fmt(self._mark_in)}-{_fmt(self._mark_out)}]")

        # Ingest clip to vector store
        self._ingest.ingest_file.__doc__  # no-op; actual clip ingestion handled by IngestController
        # Emit for parent to refresh combo boxes
        self.clip_created.emit({
            "id": clip_id,
            "start": self._mark_in,
            "end": self._mark_out,
            "summary": summary,
        })

        self._status.setText(f"切片已保存: {clip_id[:8]}…  ({_fmt(self._mark_in)}–{_fmt(self._mark_out)})")
        self._mark_in = None
        self._mark_out = None
        self._update_mark_label()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_I:
            self._mark_start()
        elif key == Qt.Key_O:
            self._mark_end()
        elif key == Qt.Key_Return or key == Qt.Key_Enter:
            self._confirm_clip()
        elif key == Qt.Key_Space:
            self._toggle_play()
        else:
            super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Timeline widget
# ---------------------------------------------------------------------------

from PyQt5.QtCore import QPoint, QRectF
from PyQt5.QtGui import QColor, QPainter, QPen


class _TimelineWidget(QWidget):
    position_clicked = pyqtSignal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._duration = 0.0
        self._playhead = 0.0
        self._segments: List[dict] = []
        self.setMinimumHeight(70)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_duration(self, duration: float) -> None:
        self._duration = max(duration, 1.0)
        self.update()

    def set_playhead(self, sec: float) -> None:
        self._playhead = sec
        self.update()

    def add_segment(self, seg: dict, duration: float) -> None:
        self._duration = max(duration, 1.0)
        self._segments.append(seg)
        self.update()

    def _sec_to_x(self, sec: float) -> int:
        if self._duration <= 0:
            return 0
        return int(sec / self._duration * self.width())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        painter.fillRect(0, 0, w, h, QColor("#1e1e2e"))

        # ASR segments
        for seg in self._segments:
            x1 = self._sec_to_x(seg["start"])
            x2 = self._sec_to_x(seg["end"])
            conf = seg.get("confidence", 0.8)
            alpha = max(80, int(conf * 200))
            color = QColor(100, 160, 255, alpha)
            painter.fillRect(x1, 10, max(x2 - x1, 2), h - 20, color)

        # Playhead
        px = self._sec_to_x(self._playhead)
        painter.setPen(QPen(QColor("#ff5555"), 2))
        painter.drawLine(px, 0, px, h)

    def mousePressEvent(self, event) -> None:
        if self._duration > 0:
            sec = event.x() / self.width() * self._duration
            self.position_clicked.emit(sec)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(sec: Optional[float]) -> str:
    if sec is None:
        return "--"
    s = int(sec)
    return f"{s // 60:02d}:{s % 60:02d}"
