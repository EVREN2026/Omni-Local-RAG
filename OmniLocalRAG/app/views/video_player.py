from typing import Optional

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtWidgets import QDialog, QPushButton, QSlider, QWidget

from app.utils.ui_loader import load_ui, require_child, resolve_layout


class VideoPlayerDialog(QDialog):
    def __init__(self, file_path: str, start_ms: int = 0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.resize(800, 500)
        self._start_ms = start_ms
        self._build(file_path)

    def _build(self, file_path: str) -> None:
        load_ui(self, "video_player.ui")
        video_layout = resolve_layout(
            self,
            host_widget_name="videoHostWidget",
            layout_name="videoHostLayout",
            ui_name="VideoPlayerDialog UI",
        )
        self._play_btn = require_child(self, QPushButton, "playButton", "VideoPlayerDialog UI")
        self._seek_bar = require_child(self, QSlider, "seekBar", "VideoPlayerDialog UI")
        self._fullscreen_btn = require_child(self, QPushButton, "fullscreenButton", "VideoPlayerDialog UI")

        self._video_widget = QVideoWidget(self)
        video_layout.addWidget(self._video_widget)

        self._player = QMediaPlayer(self, QMediaPlayer.VideoSurface)
        self._player.setVideoOutput(self._video_widget)
        self._player.setMedia(QMediaContent(QUrl.fromLocalFile(file_path)))
        self._player.mediaStatusChanged.connect(self._on_status_changed)

        self._play_btn.clicked.connect(self._toggle_play)
        self._seek_bar.setRange(0, 0)
        self._player.durationChanged.connect(self._seek_bar.setMaximum)
        self._player.positionChanged.connect(self._seek_bar.setValue)
        self._seek_bar.sliderMoved.connect(self._player.setPosition)
        self._fullscreen_btn.clicked.connect(
            lambda: self._video_widget.setFullScreen(not self._video_widget.isFullScreen())
        )

    def _on_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.LoadedMedia:
            self._player.setPosition(self._start_ms)
            self._player.play()
            self._play_btn.setText("⏸")

    def _toggle_play(self) -> None:
        if self._player.state() == QMediaPlayer.PlayingState:
            self._player.pause()
            self._play_btn.setText("▶")
        else:
            self._player.play()
            self._play_btn.setText("⏸")

    def closeEvent(self, event) -> None:
        self._player.stop()
        super().closeEvent(event)
