"""
ASRWorker — transcribes a video file using faster-whisper.
Runs in a QThread; emits per-segment progress and stores results in SQLite.
"""

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

from app.models.sqlite_store import SQLiteStore
from app.utils.logger import logger


class ASRWorker(QThread):
    segment_ready = pyqtSignal(dict)      # {"start", "end", "text", "confidence"}
    progress = pyqtSignal(float)          # 0.0–1.0 estimated progress
    finished = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)

    def __init__(self, video_path: str, model_size: str = "base") -> None:
        super().__init__()
        self.video_path = video_path
        self.model_size = model_size
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        path = Path(self.video_path)
        if not path.exists():
            self.error_occurred.emit(f"视频文件不存在: {self.video_path}")
            self.finished.emit(False)
            return

        try:
            from faster_whisper import WhisperModel  # type: ignore

            model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            segments, info = model.transcribe(
                str(path),
                beam_size=5,
                language="zh",
                vad_filter=True,
            )

            duration = info.duration or 1.0
            db = SQLiteStore()

            for seg in segments:
                if self._cancelled:
                    break
                row = {
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": seg.text.strip(),
                    "confidence": round(getattr(seg, "avg_logprob", 0.0) + 1.0, 3),
                }
                db.insert_transcript(
                    video_file=str(path.name),
                    start_sec=row["start"],
                    end_sec=row["end"],
                    text=row["text"],
                    confidence=row["confidence"],
                )
                self.segment_ready.emit(row)
                self.progress.emit(min(seg.end / duration, 1.0))

            self.finished.emit(not self._cancelled)

        except ImportError:
            self.error_occurred.emit("faster-whisper 未安装，请执行: pip install faster-whisper")
            self.finished.emit(False)
        except Exception as e:
            logger.error(f"ASRWorker error: {e}", exc_info=True)
            self.error_occurred.emit(str(e))
            self.finished.emit(False)
