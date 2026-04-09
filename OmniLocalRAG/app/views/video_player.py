"""
Inline video player dialog with seek-to-timestamp support.
"""
from typing import Optional

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


class VideoPlayerDialog(QDialog):
    def __init__(
        self,
        file_path: str,
        start_ms: int = 0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("视频播放")
        self.resize(800, 500)
        self._start_ms = start_ms
        self._build(file_path)

    def _build(self, file_path: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._video_widget = QVideoWidget()
        layout.addWidget(self._video_widget)

        self._player = QMediaPlayer(self, QMediaPlayer.VideoSurface)
        self._player.setVideoOutput(self._video_widget)
        self._player.setMedia(QMediaContent(QUrl.fromLocalFile(file_path)))
        self._player.mediaStatusChanged.connect(self._on_status_changed)

        # Controls
        ctrl = QHBoxLayout()
        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedWidth(36)
        self._play_btn.clicked.connect(self._toggle_play)

        self._seek_bar = QSlider(Qt.Horizontal)
        self._seek_bar.setRange(0, 0)
        self._player.durationChanged.connect(self._seek_bar.setMaximum)
        self._player.positionChanged.connect(self._seek_bar.setValue)
        self._seek_bar.sliderMoved.connect(self._player.setPosition)

        fullscreen_btn = QPushButton("⛶")
        fullscreen_btn.setFixedWidth(36)
        fullscreen_btn.clicked.connect(
            lambda: self._video_widget.setFullScreen(not self._video_widget.isFullScreen())
        )

        ctrl.addWidget(self._play_btn)
        ctrl.addWidget(self._seek_bar)
        ctrl.addWidget(fullscreen_btn)
        layout.addLayout(ctrl)

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
