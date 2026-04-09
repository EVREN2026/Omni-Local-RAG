"""
HotkeyController — registers a global hotkey using the `keyboard` library.
The hotkey callback fires on a background thread; we use
QMetaObject.invokeMethod to safely signal the main/UI thread.
"""

from typing import Callable, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from app.utils import config as cfg
from app.utils.logger import logger


class HotkeyController(QObject):
    triggered = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._hotkey_str = cfg.get("ui.hotkey", "alt+space")
        self._hook = None

    def register(self) -> bool:
        try:
            import keyboard  # type: ignore

            # suppress=True prevents the hotkey from being passed to other apps
            self._hook = keyboard.add_hotkey(
                self._hotkey_str,
                self._on_hotkey,
                suppress=True,
            )
            logger.info(f"Global hotkey registered: {self._hotkey_str}")
            return True
        except ImportError:
            logger.error("keyboard library not installed")
            return False
        except Exception as e:
            logger.error(f"Hotkey registration failed: {e}")
            return False

    def unregister(self) -> None:
        try:
            import keyboard  # type: ignore

            if self._hook:
                keyboard.remove_hotkey(self._hook)
                self._hook = None
        except Exception:
            pass

    def _on_hotkey(self) -> None:
        # This runs on keyboard's background thread — emit via invokeMethod
        from PyQt5.QtCore import QMetaObject, Qt

        QMetaObject.invokeMethod(self, "_emit_triggered", Qt.QueuedConnection)

    def _emit_triggered(self) -> None:
        self.triggered.emit()
