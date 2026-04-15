import gc
from pathlib import Path
from typing import Optional

from PyQt5 import uic
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QFont, QIcon, QPixmap
from PyQt5.QtWidgets import QAction, QMenu, QSystemTrayIcon

from app.utils.logger import logger
from app.utils.ui_loader import ui_path

_ICONS_DIR = Path(__file__).parent.parent.parent / "assets" / "icons"

_STATE_ICONS = {
    "ready": "tray_green.png",
    "busy": "tray_yellow.png",
    "unloaded": "tray_gray.png",
    "error": "tray_red.png",
}

# CJK-safe font stack for the tray menu.  "SF Pro Text" (the QWidget default)
# contains no CJK glyphs, so Chinese action labels render as empty boxes on
# Windows.  We override with a CJK-first family that is always present on
# Simplified-Chinese Windows installations.
_MENU_FONT = QFont()
_MENU_FONT.setFamilies(["Microsoft YaHei UI", "PingFang SC", "Segoe UI", "sans-serif"])
_MENU_FONT.setPixelSize(13)

_TrayMenuForm, _TrayMenuBase = uic.loadUiType(str(ui_path("tray_menu.ui")))


def _icon(name: str) -> QIcon:
    path = _ICONS_DIR / name
    if path.exists():
        return QIcon(str(path))
    px = QPixmap(16, 16)
    px.fill()
    return QIcon(px)


class ModelActionWorker(QThread):
    finished_with_result = pyqtSignal(bool, str, str)

    def __init__(self, action: str, parent=None) -> None:
        super().__init__(parent)
        self._action = action

    def run(self) -> None:
        from app.models.embed_manager import EmbedManager
        from app.models.llm_manager import LLMManager

        try:
            if self._action == "load":
                embed = EmbedManager()
                llm = LLMManager()
                embed_ok = embed.load()
                llm_ok = llm.load()
                if embed_ok and llm_ok:
                    self.finished_with_result.emit(True, "模型已加载：BGE-M3 和 Gemma 已就绪。", "ready")
                    return
                parts = []
                if not embed_ok:
                    parts.append(f"BGE-M3: {embed.last_error or 'load failed'}")
                if not llm_ok:
                    parts.append(f"Gemma: {llm.last_error or 'load failed'}")
                self.finished_with_result.emit(False, "模型加载失败：\n" + "\n".join(parts), "error")
                return

            if self._action == "unload":
                LLMManager().unload(reason="manual")
                EmbedManager().unload()
                gc.collect()
                self.finished_with_result.emit(True, "模型已释放：BGE-M3 和 Gemma 已卸载。", "unloaded")
                return

            self.finished_with_result.emit(False, f"未知模型操作: {self._action}", "error")
        except Exception as exc:
            logger.error(f"ModelActionWorker failed ({self._action}): {exc}", exc_info=True)
            self.finished_with_result.emit(False, f"模型操作失败: {exc}", "error")


class _TrayMenu(_TrayMenuBase, _TrayMenuForm):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.preferencesAction = QAction(self)
        self.loadModelsAction = QAction(self)
        self.freeMemoryAction = QAction(self)
        self.openEditorAction = QAction(self)
        self.separatorAction = QAction(self)
        self.quitAction = QAction(self)
        self.setupUi(self)


class TrayIcon(QSystemTrayIcon):
    def __init__(self, app, spotlight_window, knowledge_editor, parent=None) -> None:
        super().__init__(_icon(_STATE_ICONS["ready"]), parent)
        self._app = app
        self._spotlight = spotlight_window
        self._editor = knowledge_editor
        self._state = "ready"
        self._worker: Optional[ModelActionWorker] = None
        self._act_load: Optional[QAction] = None
        self._act_free: Optional[QAction] = None
        self._build_menu()
        self.setToolTip("Omni-Local RAG")
        self.activated.connect(self._on_activated)

    def _build_menu(self) -> None:
        menu = _TrayMenu()
        # Apply CJK-safe font directly so the menu shows Chinese text correctly
        # even when the global QSS font stack resolves to a Latin-only typeface.
        menu.setFont(_MENU_FONT)

        act_prefs = menu.preferencesAction
        self._act_load = menu.loadModelsAction
        self._act_free = menu.freeMemoryAction
        act_editor = menu.openEditorAction
        act_quit = menu.quitAction

        act_prefs.triggered.connect(self._open_prefs)
        self._act_load.triggered.connect(self._load_models)
        self._act_free.triggered.connect(self._free_memory)
        act_editor.triggered.connect(self._open_editor)
        act_quit.triggered.connect(self._app.quit)

        self.setContextMenu(menu)

    def set_state(self, state: str) -> None:
        if state not in _STATE_ICONS:
            return
        self._state = state
        self.setIcon(_icon(_STATE_ICONS[state]))

    def _set_actions_enabled(self, enabled: bool) -> None:
        if self._act_load is not None:
            self._act_load.setEnabled(enabled)
        if self._act_free is not None:
            self._act_free.setEnabled(enabled)

    def _start_worker(self, action: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.showMessage("Omni-Local RAG", "已有模型任务正在执行，请稍候。", QSystemTrayIcon.Information, 2000)
            return
        self.set_state("busy")
        self._set_actions_enabled(False)
        self._worker = ModelActionWorker(action, self)
        self._worker.finished_with_result.connect(self._on_model_action_finished)
        self._worker.start()

    def _on_model_action_finished(self, ok: bool, message: str, state: str) -> None:
        self.set_state(state if ok else "error")
        self._set_actions_enabled(True)
        icon = QSystemTrayIcon.Information if ok else QSystemTrayIcon.Warning
        self.showMessage("Omni-Local RAG", message, icon, 3000)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self._spotlight.toggle()

    def _open_prefs(self) -> None:
        from app.views.preferences_dialog import PreferencesDialog

        dlg = PreferencesDialog()
        dlg.exec_()

    def _load_models(self) -> None:
        self._start_worker("load")

    def _free_memory(self) -> None:
        self._start_worker("unload")

    def _open_editor(self) -> None:
        if self._editor:
            self._editor.show()
            self._editor.raise_()
