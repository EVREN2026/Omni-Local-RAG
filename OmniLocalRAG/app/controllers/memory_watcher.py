from PyQt5.QtCore import QObject, QTimer

from app.models.llm_manager import LLMManager
from app.utils import config as cfg
from app.utils.logger import logger


class MemoryWatcher(QObject):
    """
    Monitors idle time. When the Spotlight window has been hidden for
    idle_timeout_minutes, unloads the LLM to reclaim memory.
    """

    _instance = None
    _ready: bool = False  # class-level flag — safe to read before super().__init__()

    def __new__(cls, parent=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, parent=None) -> None:
        # PyQt5/sip raises RuntimeError if any instance attribute is accessed
        # (even via hasattr) before super().__init__() is called.
        # Using a class-level flag avoids touching the uninitialized C++ object.
        if MemoryWatcher._ready:
            return
        super().__init__(parent)
        MemoryWatcher._ready = True

        timeout_ms = int(cfg.get("idle_timeout_minutes", 10)) * 60 * 1000
        self._timer = QTimer(self)
        self._timer.setInterval(timeout_ms)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_idle_timeout)
        self._tray_ref = None

    def set_tray(self, tray) -> None:
        self._tray_ref = tray

    def start(self) -> None:
        self._timer.start()

    def reset(self) -> None:
        """Call on every user interaction or window show event."""
        self._timer.stop()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _on_idle_timeout(self) -> None:
        llm = LLMManager()
        if llm.is_loaded:
            llm.unload(reason="idle_timeout")
            logger.info("MemoryWatcher: LLM unloaded after idle timeout")
            if self._tray_ref:
                self._tray_ref.set_state("unloaded")
