from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal

from app.workers.ingest_worker import IngestWorker
from app.workers.asr_worker import ASRWorker
from app.utils.logger import logger


class IngestController(QObject):
    """Manages document and video ingestion workers."""

    progress = pyqtSignal(int, int)        # (current, total)
    chunk_indexed = pyqtSignal(str)
    finished = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)
    degraded_mode = pyqtSignal(str)

    # ASR-specific
    asr_segment_ready = pyqtSignal(dict)
    asr_progress = pyqtSignal(float)
    asr_finished = pyqtSignal(bool)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._ingest_worker: Optional[IngestWorker] = None
        self._asr_worker: Optional[ASRWorker] = None

    def ingest_file(self, file_path: str) -> None:
        if self._ingest_worker and self._ingest_worker.isRunning():
            logger.warning("IngestWorker already running, ignoring new request")
            return
        self._ingest_worker = IngestWorker(file_path)
        self._ingest_worker.progress.connect(self.progress)
        self._ingest_worker.chunk_indexed.connect(self.chunk_indexed)
        self._ingest_worker.finished.connect(self.finished)
        self._ingest_worker.error_occurred.connect(self.error_occurred)
        self._ingest_worker.degraded_mode.connect(self.degraded_mode)
        self._ingest_worker.start()

    def re_embed_chunk(self, chunk_id: str, new_content: str) -> None:
        """Hot-update one chunk; runs inline (called from edit workbench after save)."""
        worker = IngestWorker.__new__(IngestWorker)
        QObject.__init__(worker)
        worker.re_embed(chunk_id, new_content)

    def transcribe_video(self, video_path: str) -> None:
        if self._asr_worker and self._asr_worker.isRunning():
            self._asr_worker.cancel()
            self._asr_worker.wait(1000)
        self._asr_worker = ASRWorker(video_path)
        self._asr_worker.segment_ready.connect(self.asr_segment_ready)
        self._asr_worker.progress.connect(self.asr_progress)
        self._asr_worker.finished.connect(self.asr_finished)
        self._asr_worker.error_occurred.connect(self.error_occurred)
        self._asr_worker.start()

    def cancel_transcription(self) -> None:
        if self._asr_worker:
            self._asr_worker.cancel()
