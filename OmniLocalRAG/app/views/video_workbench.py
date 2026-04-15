import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal, QUrl
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtWidgets import (
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from app.controllers.ingest_controller import IngestController
from app.models.sqlite_store import SQLiteStore
from app.utils.logger import logger
from app.utils.ui_loader import load_ui, require_child, resolve_layout


class VideoWorkbench(QWidget):
    clip_created = pyqtSignal(dict)
    clips_loaded = pyqtSignal(list)

    def __init__(self, ingest_ctrl: IngestController, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ingest = ingest_ctrl
        self._video_path: Optional[str] = None
        self._duration_sec = 0.0
        self._segments: List[dict] = []
        self._mark_in: Optional[float] = None
        self._mark_out: Optional[float] = None
        self._player_failed = False
        self._build()
        self._connect_signals()

    def _build(self) -> None:
        load_ui(self, "video_workbench.ui")
        self._status = require_child(self, QLabel, "statusLabel", "VideoWorkbench UI")
        self._progress = require_child(self, QProgressBar, "progressBar", "VideoWorkbench UI")
        self._play_btn = require_child(self, QPushButton, "playButton", "VideoWorkbench UI")
        self._open_external_btn = require_child(self, QPushButton, "openExternalButton", "VideoWorkbench UI")
        self._pos_label = require_child(self, QLabel, "positionLabel", "VideoWorkbench UI")
        self._mark_in_btn = require_child(self, QPushButton, "markInButton", "VideoWorkbench UI")
        self._mark_out_btn = require_child(self, QPushButton, "markOutButton", "VideoWorkbench UI")
        self._confirm_btn = require_child(self, QPushButton, "confirmClipButton", "VideoWorkbench UI")
        self._mark_label = require_child(self, QLabel, "markLabel", "VideoWorkbench UI")
        self._clip_list_label = require_child(self, QLabel, "clipListLabel", "VideoWorkbench UI")

        timeline_layout = resolve_layout(
            self,
            host_widget_name="timelineHostWidget",
            layout_name="timelineHostLayout",
            ui_name="VideoWorkbench UI",
        )
        self._timeline = _TimelineWidget(self)
        self._timeline.setMinimumHeight(80)
        self._timeline.position_clicked.connect(self._seek_to)
        timeline_layout.addWidget(self._timeline)

        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.hide()
        self._play_btn.clicked.connect(self._toggle_play)
        self._open_external_btn.clicked.connect(self._open_in_system_player)
        self._mark_in_btn.clicked.connect(self._mark_start)
        self._mark_out_btn.clicked.connect(self._mark_end)
        self._confirm_btn.clicked.connect(self._confirm_clip)
        self.setFocusPolicy(Qt.StrongFocus)

        self._player = QMediaPlayer(self)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        if hasattr(self._player, "error"):
            self._player.error.connect(self._on_player_error)
        if hasattr(self._player, "errorOccurred"):
            self._player.errorOccurred.connect(self._on_player_error)

    def _connect_signals(self) -> None:
        self._ingest.asr_segment_ready.connect(self._on_asr_segment)
        self._ingest.asr_progress.connect(self._on_asr_progress)
        self._ingest.asr_finished.connect(self._on_asr_finished)

    def load_video(self, video_path: str) -> None:
        self._video_path = video_path
        self._segments = []
        self._mark_in = None
        self._mark_out = None
        self._player_failed = False
        self._status.setText(f"已加载 {Path(video_path).name} (ASR 转写中...)")
        self._progress.setValue(0)
        self._progress.show()
        self._player.setMedia(QMediaContent(QUrl.fromLocalFile(video_path)))
        probed_duration = _probe_duration_seconds(video_path)
        if probed_duration > 0:
            self._duration_sec = probed_duration
            self._timeline.set_duration(self._duration_sec)
        self._load_existing_clips()

    def _load_existing_clips(self) -> None:
        if not self._video_path:
            self.clips_loaded.emit([])
            return
        clips = SQLiteStore().get_clips_by_video(Path(self._video_path).name)
        self._clip_list_label.setText(f"已标记片段：{len(clips)} 个")
        self.clips_loaded.emit(clips)

    def _on_asr_segment(self, seg: dict) -> None:
        self._segments.append(seg)
        self._timeline.add_segment(seg, self._duration_sec)

    def _on_asr_progress(self, ratio: float) -> None:
        self._progress.setValue(int(ratio * 100))

    def _on_asr_finished(self, success: bool) -> None:
        self._progress.hide()
        self._status.setText(f"转写完成，共 {len(self._segments)} 段" if success else "转写失败")

    def _toggle_play(self) -> None:
        if not self._video_path:
            QMessageBox.warning(self, "提示", "请先导入视频")
            return
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

    def _on_media_status_changed(self, status) -> None:
        if status == QMediaPlayer.InvalidMedia:
            self._player_failed = True
            self._play_btn.setEnabled(False)
            self._status.setText("播放器不支持该视频编码。可使用“外部播放”或转码为 H264/AAC MP4。")

    def _on_player_error(self, *args) -> None:
        code = args[0] if args else None
        message = self._player.errorString() if hasattr(self._player, "errorString") else ""
        logger.error(f"Video player error: code={code}, message={message or 'unknown'}")
        self._player_failed = True
        self._play_btn.setEnabled(False)
        self._status.setText("播放器解码失败，请点“外部播放”或先转码为 H264/AAC MP4。")

    def _open_in_system_player(self) -> None:
        if not self._video_path:
            QMessageBox.warning(self, "提示", "请先导入视频")
            return
        path = str(Path(self._video_path))
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            self._status.setText("已在系统默认播放器中打开视频")
        except Exception as exc:
            logger.error(f"Open system player failed: {exc}", exc_info=True)
            QMessageBox.warning(self, "错误", f"打开系统播放器失败: {exc}")

    def _mark_start(self) -> None:
        self._mark_in = self._player.position() / 1000
        self._update_mark_label()

    def _mark_end(self) -> None:
        self._mark_out = self._player.position() / 1000
        self._update_mark_label()

    def _update_mark_label(self) -> None:
        start = _fmt(self._mark_in) if self._mark_in is not None else "--"
        end = _fmt(self._mark_out) if self._mark_out is not None else "--"
        self._mark_label.setText(f"In: {start}  Out: {end}")

    def _confirm_clip(self) -> None:
        if not self._video_path:
            QMessageBox.warning(self, "提示", "请先导入视频")
            return
        if self._mark_in is None or self._mark_out is None:
            QMessageBox.warning(self, "提示", "请先标记开始(I)和结束(O)点")
            return
        if self._mark_in >= self._mark_out:
            QMessageBox.warning(self, "提示", "结束时间必须大于开始时间")
            return

        summary, ok = QInputDialog.getText(
            self,
            "语义摘要",
            f"请输入 {_fmt(self._mark_in)} - {_fmt(self._mark_out)} 的语义摘要：",
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

        ok = self._ingest.ingest_video_clip(
            clip_id=clip_id,
            video_file=Path(self._video_path).name,
            start_sec=self._mark_in,
            end_sec=self._mark_out,
            semantic_summary=summary.strip(),
        )
        if not ok:
            QMessageBox.warning(self, "提示", "切片已保存到本地数据库，但向量化失败，请检查日志。")

        self._segments.append(
            {
                "start": self._mark_in,
                "end": self._mark_out,
                "text": summary.strip(),
                "confidence": 1.0,
                "is_clip": True,
            }
        )
        clip_count = sum(1 for seg in self._segments if seg.get("is_clip"))
        self._clip_list_label.setText(f"已标记片段：{clip_count} 个")

        self.clip_created.emit(
            {
                "id": clip_id,
                "video_file": Path(self._video_path).name if self._video_path else "",
                "start": self._mark_in,
                "end": self._mark_out,
                "summary": summary,
            }
        )

        self._status.setText(f"切片已保存 {clip_id[:8]} ({_fmt(self._mark_in)}-{_fmt(self._mark_out)})")
        self._mark_in = None
        self._mark_out = None
        self._update_mark_label()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_I:
            self._mark_start()
        elif key == Qt.Key_O:
            self._mark_end()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self._confirm_clip()
        elif key == Qt.Key_Space:
            self._toggle_play()
        else:
            super().keyPressEvent(event)


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
        width = self.width()
        height = self.height()

        painter.fillRect(0, 0, width, height, QColor("#1e1e2e"))

        for seg in self._segments:
            x1 = self._sec_to_x(seg["start"])
            x2 = self._sec_to_x(seg["end"])
            confidence = seg.get("confidence", 0.8)
            alpha = max(80, int(confidence * 200))
            color = QColor(100, 160, 255, alpha)
            painter.fillRect(x1, 10, max(x2 - x1, 2), height - 20, color)

        playhead_x = self._sec_to_x(self._playhead)
        painter.setPen(QPen(QColor("#ff5555"), 2))
        painter.drawLine(playhead_x, 0, playhead_x, height)

    def mousePressEvent(self, event) -> None:
        if self._duration > 0 and self.width() > 0:
            sec = event.x() / self.width() * self._duration
            self.position_clicked.emit(sec)


def _fmt(sec: Optional[float]) -> str:
    if sec is None:
        return "--"
    value = int(sec)
    return f"{value // 60:02d}:{value % 60:02d}"


def _probe_duration_seconds(video_path: str) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()
        return max(float(out), 0.0) if out else 0.0
    except Exception:
        return 0.0
