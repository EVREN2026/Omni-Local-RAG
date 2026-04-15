"""
Omni-Local RAG — Application Entry Point

Launch order:
1. Setup logger + faulthandler
2. Run startup dependency checks
3. Initialize SQLite schema
4. Connect ChromaStore  (non-fatal on failure)
5. Build controllers + views
7. Register global hotkey
8. Start MemoryWatcher idle timer
9. Show tray icon → app.exec_()
"""

import faulthandler
import sys
import time
import traceback
import os

# ---------------------------------------------------------------------- #
# Block ALL HuggingFace / transformers network access.
# All model files must be present locally before launch.
# startup_check.py verifies this and shows the guide if anything is missing.
# Without these env vars, sentence-transformers silently downloads missing
# files from HuggingFace Hub whenever it can't find them on disk.
# ---------------------------------------------------------------------- #
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")


# Only add the local venv torch DLL directory when this process is actually
# running inside that venv. Mixing global Python packages with venv DLLs can
# make torch fail with native DLL errors such as WinError 1114.
project_root = os.path.dirname(os.path.abspath(__file__))
venv_root = os.path.abspath(os.path.join(project_root, "venv"))
active_prefix = os.path.abspath(sys.prefix)
venv_path = os.path.join(venv_root, "Lib", "site-packages", "torch", "lib")
try:
    running_in_local_venv = os.path.commonpath([active_prefix, venv_root]) == venv_root
except ValueError:
    running_in_local_venv = False
if running_in_local_venv and os.path.exists(venv_path):
    os.add_dll_directory(venv_path)

# On Windows, importing PyQt5 before torch can make torch fail later while
# loading native DLLs (c10.dll / WinError 1114). Preloading torch here only
# initializes its runtime; the marker PDF parser still uses it lazily.
try:
    import torch  # type: ignore  # noqa: F401
except Exception:
    pass

# faulthandler prints a native backtrace on segfault / DLL abort
faulthandler.enable()

# -- Must be first PyQt5 import --
from PyQt5.QtWidgets import QApplication

from app.utils.logger import logger


def _except_hook(exc_type, exc_value, exc_tb):
    """Catch and log any unhandled Python exception before the process dies."""
    if issubclass(exc_type, KeyboardInterrupt):
        logger.info("Interrupted by user")
        return
    logger.critical("Unhandled exception — process will exit",
                    exc_info=(exc_type, exc_value, exc_tb))
    traceback.print_exception(exc_type, exc_value, exc_tb)


def main() -> int:
    sys.excepthook = _except_hook
    t_start = time.perf_counter()

    app = QApplication(sys.argv)
    app.setApplicationName("Omni-Local RAG")
    app.setQuitOnLastWindowClosed(False)  # keep alive after all windows closed

    # ------------------------------------------------------------------ #
    # 1. Dependency checks
    # ------------------------------------------------------------------ #
    logger.info("Step 1/7 — startup checks")
    try:
        from app.utils import startup_check
        startup_check.run_all()
        missing = startup_check.missing_items()
        if missing:
            from app.views.startup_guide import StartupGuideDialog
            dlg = StartupGuideDialog(missing)
            if dlg.exec_() != StartupGuideDialog.Accepted:
                logger.info("User closed startup guide — exiting")
                return 1
    except Exception:
        logger.error("Startup check failed", exc_info=True)

    # ------------------------------------------------------------------ #
    # 2. SQLite
    # ------------------------------------------------------------------ #
    logger.info("Step 2/7 — SQLite init")
    try:
        from app.models.sqlite_store import SQLiteStore
        SQLiteStore().init()
    except Exception:
        logger.error("SQLiteStore init failed", exc_info=True)

    # ------------------------------------------------------------------ #
    # 3. ChromaStore  (non-fatal)
    # ------------------------------------------------------------------ #
    logger.info("Step 3/7 — ChromaStore connect")
    try:
        from app.models.chroma_store import ChromaStore
        if not ChromaStore().connect():
            logger.warning("ChromaStore unavailable — search disabled until restart")
    except Exception:
        logger.error("ChromaStore connect raised exception", exc_info=True)

    # ------------------------------------------------------------------ #
    # 4. LLM (lazy)
    # ------------------------------------------------------------------ #
    logger.info("Step 4/7 — LLM will load on first use")

    # ------------------------------------------------------------------ #
    # 5. Controllers
    # ------------------------------------------------------------------ #
    logger.info("Step 5/7 — creating controllers")
    from app.controllers.ingest_controller import IngestController
    from app.controllers.search_controller import SearchController
    from app.controllers.hotkey_controller import HotkeyController
    from app.controllers.memory_watcher import MemoryWatcher

    ingest_ctrl = IngestController()
    search_ctrl = SearchController()
    hotkey_ctrl = HotkeyController()

    # ------------------------------------------------------------------ #
    # 6. Views
    # ------------------------------------------------------------------ #
    logger.info("Step 6/7 — creating views")
    from app.views.knowledge_editor import KnowledgeEditor
    from app.views.spotlight_window import SpotlightWindow
    from app.views.tray_icon import TrayIcon

    editor   = KnowledgeEditor(ingest_ctrl, search_ctrl)
    spotlight = SpotlightWindow()
    tray     = TrayIcon(app, spotlight, editor)

    # ------------------------------------------------------------------ #
    # 7. MemoryWatcher + hotkey + tray
    # ------------------------------------------------------------------ #
    logger.info("Step 7/7 — hotkey, MemoryWatcher, tray")
    watcher = MemoryWatcher()
    watcher.set_tray(tray)
    watcher.start()

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

    from PyQt5.QtWidgets import QSystemTrayIcon
    if not QSystemTrayIcon.isSystemTrayAvailable():
        logger.warning("System tray not available on this desktop — tray icon hidden")

    tray.show()

    # ── Ensure llama-server is stopped on exit ──
    def _cleanup():
        try:
            from app.models.llm_manager import LLMManager
            LLMManager().unload(reason="app_exit")
        except Exception:
            pass

    import atexit
    atexit.register(_cleanup)
    app.aboutToQuit.connect(_cleanup)

    elapsed_ms = (time.perf_counter() - t_start) * 1000
    logger.info(f"Application ready in {elapsed_ms:.0f} ms — entering event loop")

    return app.exec_()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        logger.critical("Fatal error in main()", exc_info=True)
        input("Press Enter to close …")   # keep window open so user can read it
        sys.exit(1)
