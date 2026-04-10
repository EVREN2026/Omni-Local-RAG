"""
IngestWorker — chunks a document, embeds it and stores into ChromaDB.
Supports PDF (via Docling + fallback OCR) and manual video clip ingestion.
"""

import json
import uuid
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from app.models.chroma_store import ChromaStore
from app.models.embed_manager import EmbedManager
from app.utils.logger import logger

_MAX_CHUNK_TOKENS = 512
_OVERLAP_TOKENS = 64


def _rough_split(text: str, max_tokens: int = _MAX_CHUNK_TOKENS, overlap: int = _OVERLAP_TOKENS) -> List[str]:
    """Naïve whitespace-based splitter (replace with tiktoken for production)."""
    words = text.split()
    chunks, start = [], 0
    while start < len(words):
        end = start + max_tokens
        chunks.append(" ".join(words[start:end]))
        start = end - overlap
    return chunks


def _parse_pdf(file_path: str):
    """Returns list of (text, page_num, coords, heading_level)."""
    results = []
    try:
        from docling.document_converter import DocumentConverter  # type: ignore

        converter = DocumentConverter()
        doc = converter.convert(file_path)
        for item in doc.document.iterate_items():
            text = getattr(item, "text", "") or ""
            page = getattr(item, "page_no", 0) or 0
            if text.strip():
                results.append((text.strip(), page, [], ""))
    except Exception as e:
        logger.warning(f"Docling failed ({e}), falling back to OCR")
        results = _ocr_fallback(file_path)
    return results


def _ocr_fallback(file_path: str):
    """Minimal OCR using PyMuPDF + pytesseract."""
    results = []
    try:
        import fitz  # type: ignore

        doc = fitz.open(file_path)
        for page in doc:
            text = page.get_text()
            if text.strip():
                results.append((text.strip(), page.number, [], ""))
    except Exception as e:
        logger.error(f"OCR fallback also failed: {e}")
    return results


class IngestWorker(QThread):
    progress = pyqtSignal(int, int)       # (current, total)
    chunk_indexed = pyqtSignal(str)       # chunk_id
    finished = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)
    degraded_mode = pyqtSignal(str)       # message for UI banner

    def __init__(self, file_path: str) -> None:
        super().__init__()
        self.file_path = file_path

    def run(self) -> None:
        path = Path(self.file_path)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            self._ingest_pdf()
        else:
            self.error_occurred.emit(f"不支持的文件类型: {suffix}")
            self.finished.emit(False)

    def _ingest_pdf(self) -> None:
        embed = EmbedManager()
        chroma = ChromaStore()

        is_degraded = False
        try:
            items = _parse_pdf(self.file_path)
        except Exception as e:
            self.error_occurred.emit(str(e))
            self.finished.emit(False)
            return

        if not items:
            self.degraded_mode.emit("已降级为基础 OCR 模式（未提取到结构化内容）")
            is_degraded = True
            items = _ocr_fallback(self.file_path)

        chunks: List[dict] = []
        for text, page, coords, _ in items:
            for chunk_text in _rough_split(text):
                if chunk_text.strip():
                    chunks.append({
                        "id": str(uuid.uuid4()),
                        "content": chunk_text,
                        "page": page,
                        "coords": coords,
                    })

        total = len(chunks)
        logger.info(f"IngestWorker: {total} chunks from {self.file_path}")

        export_rows: List[dict] = []
        for i, chunk in enumerate(chunks):
            try:
                [vec] = embed.encode([chunk["content"]])
                chroma.add(
                    content=chunk["content"],
                    embedding=vec,
                    source_type="pdf",
                    anchor_id=chunk["id"],
                    pdf_payload={
                        "file": str(Path(self.file_path).name),
                        "page": chunk["page"],
                        "coords": chunk["coords"],
                    },
                    is_manual=False,
                    doc_id=chunk["id"],
                )
                self.chunk_indexed.emit(chunk["id"])
                self.progress.emit(i + 1, total)
                export_rows.append(
                    {
                        "chunk_id": chunk["id"],
                        "anchor_id": chunk["id"],
                        "source_type": "pdf",
                        "page": chunk["page"],
                        "coords": chunk["coords"],
                        "content": chunk["content"],
                        "vector_dim": len(vec),
                        "stored": True,
                        "is_manual": False,
                    }
                )
            except Exception as e:
                logger.error(f"Chunk ingest failed: {e}", exc_info=True)

        try:
            from app.models.metadata_exporter import MetadataExporter

            MetadataExporter().export_pdf_chunks(
                source_file=self.file_path,
                chunks=export_rows,
            )
        except Exception as e:
            logger.error(f"PDF metadata export failed: {e}", exc_info=True)

        if is_degraded:
            self.degraded_mode.emit("已降级为基础 OCR 模式")
        self.finished.emit(True)

    def re_embed(self, chunk_id: str, new_content: str) -> None:
        """Hot-update a single chunk (called from EditController, runs in same thread)."""
        embed = EmbedManager()
        chroma = ChromaStore()
        try:
            [vec] = embed.encode([new_content])
            chroma.update(chunk_id, new_content, vec)
            logger.info(f"re_embed: chunk {chunk_id} updated")
        except Exception as e:
            logger.error(f"re_embed failed: {e}", exc_info=True)
