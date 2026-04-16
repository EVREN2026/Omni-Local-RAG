"""
IngestWorker — chunks a document and stores results.
Supports marker PDF parsing plus manual video clip ingestion.

Chunking logic lives in app.models.chunker (public API); this module
delegates to it and adds QThread signals.

Embedding logic has been removed — the store now uses heading_path and
category for LLM-directed retrieval instead of vector similarity.
"""

import re
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from app.models.chroma_store import ChromaStore
from app.models.chunker import (
    ChunkItem,
    coerce_chunk_item,
    _extract_marker_page_number,   # used below for compat
    _markdown_to_items as _chunker_markdown_to_items,
    _parse_markdown_heading,
    markdown_to_items,
)
from app.models.stable_ids import stable_chunk_id
from app.parsers.document_parsers import PARSER_REGISTRY
from app.utils import config as cfg
from app.utils.category_keywords import heuristic_category
from app.utils.logger import logger

_DEFAULT_MARKDOWN_MAX_CHARS = 1800
_DEFAULT_MARKDOWN_OVERLAP_CHARS = 240


def _parse_pdf(file_path: str):
    """Returns list of (text, page_num, coords, heading_level)."""
    name = _selected_parser_name()
    parser = PARSER_REGISTRY.get(name)
    if parser is None:
        raise RuntimeError(f"未知解析器: {name}")
    results = parser.parse(file_path)
    if results:
        logger.info(f"PDF parsed with {name}: {len(results)} text blocks")
    return results


def _pdf_parser_order() -> List[str]:
    order = cfg.get("pdf.parser_order", ["marker"])
    if not isinstance(order, list):
        return ["marker"]
    normalized = [str(name).strip().lower() for name in order if str(name).strip()]
    return normalized or ["marker"]


def _selected_parser_name() -> str:
    return _pdf_parser_order()[0]


def _safe_export_stem(source_file: str) -> str:
    stem = Path(source_file).stem or "document"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "document"


def _converted_markdown_path(source_file: str) -> Path:
    root = Path(cfg.get("exports.path", "data/exports"))
    root = root if root.is_absolute() else cfg.abs_path(str(root))
    return root / "pdf" / f"{_safe_export_stem(source_file)}.converted.md"


def _markdown_to_items(markdown: str) -> List[tuple]:
    """Delegate to the public chunker API (app.models.chunker)."""
    return markdown_to_items(markdown)


# Thin wrappers kept for any external callers that imported these directly.
from app.models.chunker import (  # noqa: E402
    _split_markdown_section,
    _find_markdown_boundary,
)


class IngestWorker(QThread):
    progress = pyqtSignal(int, int)            # (current, total) progress
    chunk_indexed = pyqtSignal(str)            # chunk_id
    finished = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)
    degraded_mode = pyqtSignal(str, str, str)  # (attempted, fallback, reason)
    stage_changed = pyqtSignal(str)            # human-readable stage description
    page_progress = pyqtSignal(int, int)       # (current_page, total_pages)
    parse_done = pyqtSignal(str, int)          # (parser_name_used, num_blocks)

    def __init__(self, file_path: str) -> None:
        super().__init__()
        self.file_path = file_path

    def run(self) -> None:
        path = Path(self.file_path)
        suffix = path.suffix.lower()
        if suffix in {".md", ".markdown"}:
            self._ingest_markdown()
        else:
            self._ingest_pdf()

    def _ingest_pdf(self) -> None:
        try:
            md_path = self._convert_file_to_markdown(self.file_path)
        except Exception as e:
            self.error_occurred.emit(str(e))
            self.finished.emit(False)
            return

        if not md_path.exists() or not md_path.read_text(encoding="utf-8").strip():
            self.error_occurred.emit("No markdown content produced. Please check parser support and dependencies.")
            self.finished.emit(False)
            return

        self.stage_changed.emit(f"转换完成: {md_path.name}")
        self.progress.emit(1, 1)
        self.finished.emit(True)

    def _convert_file_to_markdown(self, file_path: str) -> Path:
        """Convert selected file to raw markdown; no chunking here."""
        parser_name = _selected_parser_name()
        parser = PARSER_REGISTRY.get(parser_name)
        if parser is None:
            raise RuntimeError(f"Unknown parser: {parser_name}")

        md_path = _converted_markdown_path(file_path)

        # Emit total page count once (requires fitz; skip silently if unavailable)
        try:
            import fitz  # type: ignore

            _doc = fitz.open(file_path)
            total_pages = len(_doc)
            _doc.close()
            self.page_progress.emit(0, total_pages)
        except Exception:
            total_pages = 0

        self.stage_changed.emit(f"Parsing with {parser_name}...")
        markdown = parser.to_markdown(file_path)

        if total_pages:
            self.page_progress.emit(total_pages, total_pages)

        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(markdown, encoding="utf-8")
        self.parse_done.emit(parser_name, 1 if markdown.strip() else 0)
        logger.info(f"File converted with {parser_name}: {md_path}")
        return md_path

    def _parse_pdf_with_signals(self, file_path: str) -> list:
        """Compatibility helper for tests and benchmark callers."""
        md_path = self._convert_file_to_markdown(file_path)
        markdown = md_path.read_text(encoding="utf-8")
        return _markdown_to_items(markdown)

    def _ingest_markdown(self) -> None:
        try:
            markdown = Path(self.file_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            markdown = Path(self.file_path).read_text(encoding="utf-8-sig")
        except Exception as e:
            self.error_occurred.emit(f"Markdown 读取失败: {e}")
            self.finished.emit(False)
            return

        items = _markdown_to_items(markdown)
        if not items:
            self.error_occurred.emit("Markdown has no indexable content.")
            self.finished.emit(False)
            return

        self._ingest_items(items, source_type="markdown")

    def _ingest_items(self, items: List[tuple], source_type: str, index_vectors: bool = True) -> None:
        self.stage_changed.emit("Chunking...")
        chroma = ChromaStore() if index_vectors else None

        chunks: List[dict] = []
        used_ids = set()
        for item in items:
            chunk_item = coerce_chunk_item(item)
            legacy_text = chunk_item.legacy_text
            # No more rough_split — chunker handles splitting
            display_content = chunk_item.display_content
            embedding_text = chunk_item.embedding_text

            chunk_id = stable_chunk_id(
                source_file=self.file_path,
                source_type=source_type,
                page=chunk_item.page,
                heading_path=chunk_item.heading_path,
                content=display_content,
                used_ids=used_ids,
            )
            chunks.append(
                {
                    "id": chunk_id,
                    "content": display_content.strip(),
                    "display_content": display_content.strip(),
                    "embedding_text": (embedding_text or display_content).strip(),
                    "page": chunk_item.page,
                    "coords": chunk_item.coords,
                    "heading_path": chunk_item.heading_path,
                    "block_type": chunk_item.block_type,
                    "metadata": dict(chunk_item.metadata or {}),
                }
            )

        total = len(chunks)
        logger.info(f"IngestWorker: {total} {source_type} chunks from {self.file_path}")

        self.stage_changed.emit(f"Storing {total} chunk(s)...")

        export_rows: List[dict] = []
        for i, chunk in enumerate(chunks):
            try:
                stored = False
                if index_vectors and chroma is not None:
                    heading_path = chunk.get("heading_path", "")
                    content = chunk["content"]
                    category_blob = f"{Path(self.file_path).name} {heading_path} {content[:300]}"
                    chunk_category = heuristic_category(category_blob)

                    payload = {
                        "file": str(Path(self.file_path).name),
                        "page": chunk["page"],
                        "coords": chunk["coords"],
                        "heading_path": heading_path,
                        "block_type": chunk.get("block_type", "text"),
                        "display_content": chunk.get("display_content", chunk["content"]),
                        "embedding_text": chunk.get("embedding_text", chunk["content"]),
                        "metadata": chunk.get("metadata", {}),
                    }
                    chroma.add(
                        content=content,
                        source_type=source_type,
                        anchor_id=chunk["id"],
                        pdf_payload=payload,
                        is_manual=False,
                        doc_id=chunk["id"],
                        category=chunk_category,
                        heading_path=heading_path,
                    )
                    self.chunk_indexed.emit(chunk["id"])
                    stored = True
                self.progress.emit(i + 1, total)
                export_rows.append(
                    {
                        "chunk_id": chunk["id"],
                        "anchor_id": chunk["id"],
                        "source_type": source_type,
                        "page": chunk["page"],
                        "coords": chunk["coords"],
                        "heading_path": chunk.get("heading_path", ""),
                        "block_type": chunk.get("block_type", "text"),
                        "content": chunk["content"],
                        "display_content": chunk.get("display_content", chunk["content"]),
                        "embedding_text": chunk.get("embedding_text", chunk["content"]),
                        "metadata": chunk.get("metadata", {}),
                        "stored": stored,
                        "is_manual": False,
                    }
                )
            except Exception as e:
                logger.error(f"Chunk ingest failed: {e}", exc_info=True)

        if total and not export_rows:
            self.error_occurred.emit("Chunk storage failed")
            self.finished.emit(False)
            return

        try:
            from app.models.metadata_exporter import MetadataExporter

            MetadataExporter().export_chunks(
                source_file=self.file_path,
                chunks=export_rows,
                source_type=source_type,
            )
        except Exception as e:
            logger.error(f"{source_type} metadata export failed: {e}", exc_info=True)

        self.stage_changed.emit("导入完成")
        self.finished.emit(True)

        # Update knowledge graph incrementally
        try:
            from app.models.graph_updater import GraphUpdater
            updater = GraphUpdater()
            updater.on_chunks_added(export_rows, source_file=self.file_path)
            updater.maybe_recluster()
        except Exception as e:
            logger.debug(f"Graph update after ingest skipped: {e}")
