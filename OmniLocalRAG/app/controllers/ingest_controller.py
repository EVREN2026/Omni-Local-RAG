from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal

from app.workers.ingest_worker import IngestWorker
from app.workers.asr_worker import ASRWorker
from app.models.chroma_store import ChromaStore
from app.models.embed_manager import EmbedManager
from app.models.sqlite_store import SQLiteStore
from app.utils.logger import logger


class IngestController(QObject):
    """Manages document and video ingestion workers."""

    progress = pyqtSignal(int, int)        # (current, total)
    chunk_indexed = pyqtSignal(str)
    finished = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)
    degraded_mode = pyqtSignal(str, str, str)  # (attempted, fallback, reason)
    stage_changed = pyqtSignal(str)
    page_progress = pyqtSignal(int, int)
    parse_done = pyqtSignal(str, int)

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
        self._ingest_worker.stage_changed.connect(self.stage_changed)
        self._ingest_worker.page_progress.connect(self.page_progress)
        self._ingest_worker.parse_done.connect(self.parse_done)
        self._ingest_worker.start()

    def re_embed_chunk(self, chunk_id: str, new_content: str) -> None:
        """Hot-update one chunk; runs inline (called from edit workbench after save)."""
        # re_embed() only uses EmbedManager/ChromaStore singletons — no threading needed.
        # Pass empty file_path; IngestWorker.__init__ stores it but re_embed never reads it.
        IngestWorker("").re_embed(chunk_id, new_content)

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

    def ingest_video_clip(
        self,
        clip_id: str,
        video_file: str,
        start_sec: float,
        end_sec: float,
        semantic_summary: str,
    ) -> bool:
        """Embed a saved video clip into vector store and backfill chroma_id."""
        try:
            embed = EmbedManager()
            if hasattr(embed, "load") and not getattr(embed, "is_loaded", False):
                if not embed.load():
                    detail = getattr(embed, "last_error", "") or "请检查 PyTorch / sentence-transformers 环境"
                    self.error_occurred.emit(f"Embedding 模型加载失败：{detail}")
                    return False

            text = (
                f"[视频切片] 文件: {video_file}\n"
                f"时间: {start_sec:.2f}s - {end_sec:.2f}s\n"
                f"摘要: {semantic_summary}"
            )
            [vec] = embed.encode([text])
            ChromaStore().add(
                content=text,
                embedding=vec,
                source_type="video",
                anchor_id=clip_id,
                video_payload={
                    "file": video_file,
                    "start": start_sec,
                    "end": end_sec,
                },
                is_manual=True,
                doc_id=clip_id,
            )
            SQLiteStore().update_clip(clip_id, semantic_summary, chroma_id=clip_id)
            self.chunk_indexed.emit(clip_id)
            return True
        except Exception as e:
            logger.error(f"ingest_video_clip failed: {e}", exc_info=True)
            self.error_occurred.emit(f"视频切片向量化失败: {e}")
            return False
