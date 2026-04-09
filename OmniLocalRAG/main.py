"""
Omni-Local RAG — Application Entry Point

Launch order:
1. Setup logger
2. Run startup dependency checks
3. Initialize SQLite schema
4. Start PyQt5 application
5. Show StartupGuideDialog if files are missing, else main window
6. Register global hotkey
7. Start MemoryWatcher idle timer
"""

import sys
import time

# -- Must be first PyQt5 import --
from PyQt5.QtWidgets import QApplication

from app.utils.logger import logger


def main() -> int:
    t_start = time.perf_counter()

    app = QApplication(sys.argv)
    app.setApplicationName("Omni-Local RAG")
    app.setQuitOnLastWindowClosed(False)  # keep alive after all windows closed

    # --- Dependency checks ---
    from app.utils import startup_check
    startup_check.run_all()
    missing = startup_check.missing_items()
    if missing:
        from app.views.startup_guide import StartupGuideDialog
        dlg = StartupGuideDialog(missing)
        if dlg.exec_() != StartupGuideDialog.Accepted:
            return 1

    # --- SQLite init ---
    from app.models.sqlite_store import SQLiteStore
    SQLiteStore().init()

    # --- Connect to ChromaDB ---
    from app.models.chroma_store import ChromaStore
    ChromaStore().connect()

    # --- Load BGE-M3 (resident) ---
    from app.models.embed_manager import EmbedManager
    EmbedManager().load()

    # --- Controllers ---
    from app.controllers.ingest_controller import IngestController
    from app.controllers.search_controller import SearchController
    from app.controllers.hotkey_controller import HotkeyController
    from app.controllers.memory_watcher import MemoryWatcher

    ingest_ctrl = IngestController()
    search_ctrl = SearchController()
    hotkey_ctrl = HotkeyController()

    # --- Views ---
    from app.views.knowledge_editor import KnowledgeEditor
    from app.views.spotlight_window import SpotlightWindow
    from app.views.tray_icon import TrayIcon

    editor = KnowledgeEditor(ingest_ctrl, search_ctrl)
    spotlight = SpotlightWindow()
    tray = TrayIcon(app, spotlight, editor)

    # --- MemoryWatcher ---
    watcher = MemoryWatcher()
    watcher.set_tray(tray)
    watcher.start()

    # --- Hotkey ---
    registered = hotkey_ctrl.register()
    hotkey_ctrl.triggered.connect(spotlight.toggle)
    if not registered:
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.warning(
            None,
            "热键注册失败",
            f"无法注册全局热键 [{hotkey_ctrl._hotkey_str}]。\n"
            "可能被其他软件占用，请在 config.json 中更换热键组合。",
        )

    tray.show()

    elapsed_ms = (time.perf_counter() - t_start) * 1000
    logger.info(f"Application started in {elapsed_ms:.0f}ms")

    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
