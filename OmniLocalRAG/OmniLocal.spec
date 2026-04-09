# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Omni-Local RAG
# Build: pyinstaller OmniLocal.spec

import sys
from pathlib import Path

block_cipher = None

# Collect all hidden imports that PyInstaller misses
hidden_imports = [
    "chromadb",
    "chromadb.db.impl",
    "chromadb.segment",
    "chromadb.segment.impl.manager",
    "sentence_transformers",
    "huggingface_hub",
    "tokenizers",
    "torch",
    "tqdm",
    "docling",
    "docling.document_converter",
    "faster_whisper",
    "keyboard",
    "fitz",        # PyMuPDF
    "pytesseract",
    "librosa",
    "soundfile",
    "PyQt5.QtMultimedia",
    "PyQt5.QtMultimediaWidgets",
    "sqlite3",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[
        # llama.dll — must be in the same folder as the EXE
        # ("path/to/llama.dll", "."),
    ],
    datas=[
        ("assets/", "assets/"),
        ("config.json", "."),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OmniLocal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no console window
    icon="assets/icons/app.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="OmniLocal",
)
