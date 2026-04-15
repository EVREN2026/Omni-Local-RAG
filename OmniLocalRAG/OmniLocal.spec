# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Omni-Local RAG
# Build command (run from OmniLocalRAG\ directory):
#   pyinstaller OmniLocal.spec --noconfirm
#   OR double-click build.bat

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ---------------------------------------------------------------------------
# Data files from third-party packages
# ---------------------------------------------------------------------------
_extra_datas = (
    collect_data_files("sentence_transformers")
    + collect_data_files("chromadb")
    + collect_data_files("tokenizers")
)

# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
hidden_imports = [
    # jaraco — required by pkg_resources (setuptools 71+); PyInstaller misses it
    "jaraco.text",
    "jaraco.functools",
    "jaraco.context",
    "jaraco.collections",
    # Our own app modules (all subpackages)
    *collect_submodules("app"),
    # chromadb 0.5.x — heavy use of importlib dynamic loading
    "chromadb",
    "chromadb.api",
    "chromadb.api.segment",
    "chromadb.api.types",
    "chromadb.config",
    "chromadb.db.impl",
    "chromadb.db.impl.sqlite",
    "chromadb.segment",
    "chromadb.segment.impl",
    "chromadb.segment.impl.manager",
    "chromadb.segment.impl.manager.local",
    "chromadb.segment.impl.metadata",
    "chromadb.segment.impl.metadata.sqlite",
    "chromadb.segment.impl.vector",
    "chromadb.segment.impl.vector.local_hnsw",
    "chromadb.telemetry",
    "chromadb.telemetry.product",
    "chromadb.telemetry.product.posthog",
    "chromadb.types",
    "chromadb.utils",
    "chromadb.utils.embedding_functions",
    # sentence_transformers / transformers
    "sentence_transformers",
    "sentence_transformers.models",
    "transformers",
    "huggingface_hub",
    "tokenizers",
    # PyTorch
    "torch",
    "torch.nn",
    "torch.nn.functional",
    "torch.utils",
    "torch.utils.data",
    # PyMuPDF
    "fitz",
    # faster-whisper / CTranslate2
    "faster_whisper",
    "ctranslate2",
    # docling
    "docling",
    "docling.document_converter",
    # PyQt5 multimedia (video player)
    "PyQt5.QtMultimedia",
    "PyQt5.QtMultimediaWidgets",
    # stdlib
    "sqlite3",
    "logging.handlers",
    "uuid",
    "gc",
    # keyboard global hook
    "keyboard",
]

# ---------------------------------------------------------------------------
# Icon (optional — place a 256x256 ICO at assets/icons/app.ico)
# ---------------------------------------------------------------------------
_icon_path = Path("assets/icons/app.ico")

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("assets/", "assets/"),
        ("config.json", "."),
        *_extra_datas,
        # NOTE: models/ and data/ are NOT bundled.
        # After building, copy models\ into dist\OmniLocal\ manually.
        # The data\ directory (ChromaDB + SQLite) is created at runtime.
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "onnxruntime",           # we never use chromadb's built-in embedder
        "onnxruntime.backend",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OmniLocal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                   # UPX can corrupt native DLLs
    console=False,               # no console window
    icon=str(_icon_path) if _icon_path.exists() else None,
    contents_directory=".",      # flatten _internal into dist dir (avoids build-path DLL error)
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="OmniLocal",
)
