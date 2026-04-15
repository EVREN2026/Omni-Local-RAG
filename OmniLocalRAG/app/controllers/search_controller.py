from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal

from app.controllers.memory_watcher import MemoryWatcher
from app.workers.inference_worker import InferenceWorker
from app.utils.logger import logger


class SearchController(QObject):
    """Manages the lifecycle of InferenceWorker for a single query."""

    context_ready = pyqtSignal(list)
    token_ready = pyqtSignal(str)
    timings_ready = pyqtSignal(dict)
    search_finished = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[InferenceWorker] = None
        self._cache_invalid_ids: set = set()

    def search(self, query: str) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(500)

        MemoryWatcher().reset()

        self._worker = InferenceWorker(query)
        self._worker.context_retrieved.connect(self._on_context)
        self._worker.timings_ready.connect(self.timings_ready)
        self._worker.token_generated.connect(self.token_ready)
        self._worker.generation_finished.connect(self.search_finished)
        self._worker.error_occurred.connect(self.error_occurred)
        self._worker.start()

    def cancel(self) -> None:
        if self._worker:
            self._worker.cancel()

    def invalidate_cache(self, chunk_id: str) -> None:
        self._cache_invalid_ids.add(chunk_id)

    def _on_context(self, results: list) -> None:
        # Filter out stale cache entries so updated content surfaces immediately
        filtered = [r for r in results if r.get("id") not in self._cache_invalid_ids]
        self.context_ready.emit(filtered)
        self._cache_invalid_ids.clear()
