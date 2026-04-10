from pathlib import Path
from typing import Optional

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QAction, QMenu, QSystemTrayIcon

from app.utils import config as cfg
from app.utils.logger import logger

_ICONS_DIR = Path(__file__).parent.parent.parent / "assets" / "icons"

_STATE_ICONS = {
    "ready":    "tray_green.png",
    "busy":     "tray_yellow.png",
    "unloaded": "tray_gray.png",
    "error":    "tray_red.png",
}


def _icon(name: str) -> QIcon:
    path = _ICONS_DIR / name
    if path.exists():
        return QIcon(str(path))
    # Fallback: empty icon
    from PyQt5.QtGui import QPixmap
    px = QPixmap(16, 16)
    px.fill()
    return QIcon(px)


class TrayIcon(QSystemTrayIcon):
    def __init__(self, app, spotlight_window, knowledge_editor, parent=None) -> None:
        super().__init__(_icon(_STATE_ICONS["ready"]), parent)
        self._app = app
        self._spotlight = spotlight_window
        self._editor = knowledge_editor
        self._state = "ready"
        self._build_menu()
        self.setToolTip("Omni-Local RAG")
        self.activated.connect(self._on_activated)

    def _build_menu(self) -> None:
        menu = QMenu()

        act_prefs = QAction("偏好设置", self)
        act_prefs.triggered.connect(self._open_prefs)
        menu.addAction(act_prefs)

        act_free = QAction("立即释放内存", self)
        act_free.triggered.connect(self._free_memory)
        menu.addAction(act_free)

        act_editor = QAction("打开知识库编辑器", self)
        act_editor.triggered.connect(self._open_editor)
        menu.addAction(act_editor)

        menu.addSeparator()

        act_quit = QAction("完全退出", self)
        act_quit.triggered.connect(self._app.quit)
        menu.addAction(act_quit)

        self.setContextMenu(menu)

    def set_state(self, state: str) -> None:
        if state not in _STATE_ICONS:
            return
        self._state = state
        self.setIcon(_icon(_STATE_ICONS[state]))

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self._spotlight.toggle()

    def _open_prefs(self) -> None:
        from app.views.preferences_dialog import PreferencesDialog
        dlg = PreferencesDialog()
        dlg.exec_()

    def _free_memory(self) -> None:
        from app.models.llm_manager import LLMManager
        import gc
        LLMManager().unload(reason="manual")
        gc.collect()
        self.set_state("unloaded")
        self.showMessage("Omni-Local RAG", "内存已释放", QSystemTrayIcon.Information, 2000)

    def _open_editor(self) -> None:
        if self._editor:
            self._editor.show()
            self._editor.raise_()
