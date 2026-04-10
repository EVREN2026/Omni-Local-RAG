"""
IngestWorker — chunks a document, embeds it, and stores results.
Supports manual document parsing (docling/unstructured/mineru/marker/ocr),
plus manual video clip ingestion.

Chunking logic lives in app.models.chunker (public API); this module
delegates to it and adds QThread signals + PDF page-image export.
"""

import re
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from app.models.chroma_store import ChromaStore
from app.models.chunker import (
    _extract_marker_page_number,   # used below for compat
    _markdown_to_items as _chunker_markdown_to_items,
    _parse_markdown_heading,
    markdown_to_items,
)
from app.models.embed_manager import EmbedManager
from app.models.stable_ids import stable_chunk_id
from app.parsers.document_parsers import PARSER_REGISTRY
from app.utils import config as cfg
from app.utils.logger import logger

_MAX_CHUNK_TOKENS = 512
_OVERLAP_TOKENS = 64
_DEFAULT_MARKDOWN_MAX_CHARS = 1800
_DEFAULT_MARKDOWN_OVERLAP_CHARS = 240
_PAGE_IMAGE_SCALE = 1.5


def _rough_split(text: str, max_tokens: int = _MAX_CHUNK_TOKENS, overlap: int = _OVERLAP_TOKENS) -> List[str]:
    """Naive whitespace-based splitter (replace with tiktoken for production)."""
    words = text.split()
    chunks, start = [], 0
    while start < len(words):
        end = start + max_tokens
        chunks.append(" ".join(words[start:end]))
        start = end - overlap
    return chunks


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
    order = cfg.get("pdf.parser_order", ["docling", "unstructured", "mineru", "marker", "ocr"])
    if not isinstance(order, list):
        return ["docling", "unstructured", "mineru", "marker", "ocr"]
    normalized = [str(name).strip().lower() for name in order if str(name).strip()]
    return normalized or ["docling", "unstructured", "mineru", "marker", "ocr"]


def _selected_parser_name() -> str:
    return _pdf_parser_order()[0]


def _safe_export_stem(source_file: str) -> str:
    stem = Path(source_file).stem or "document"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "document"


def _converted_markdown_path(source_file: str) -> Path:
    root = Path(cfg.get("exports.path", "data/exports"))
    root = root if root.is_absolute() else cfg.abs_path(str(root))
    return root / "pdf" / f"{_safe_export_stem(source_file)}.converted.md"


def _converted_image_dir(source_file: str) -> Path:
    md_path = _converted_markdown_path(source_file)
    return md_path.with_name(f"{_safe_export_stem(source_file)}_images")


def _markdown_has_image_link(markdown: str) -> bool:
    md_pattern = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
    html_pattern = re.compile(r"<img[^>]+src=[\"'][^\"']+[\"']", re.IGNORECASE)
    return bool(md_pattern.search(markdown) or html_pattern.search(markdown))


def _inject_page_images_if_needed(markdown: str, image_dir_name: str, total_pages: int) -> str:
    if not markdown.strip():
        return markdown
    if _markdown_has_image_link(markdown):
        return markdown

    marker_pattern = re.compile(r"<!--\s*page:\s*(\d+)\s*-->", re.IGNORECASE)
    matched = False

    def _replace_marker(match: re.Match) -> str:
        nonlocal matched
        matched = True
        page_no = int(match.group(1))
        image_rel = f"{image_dir_name}/page_{page_no:04d}.png"
        return f"{match.group(0)}\n\n![page_{page_no}]({image_rel})"

    updated = marker_pattern.sub(_replace_marker, markdown)
    if matched:
        return updated

    if total_pages <= 0:
        return markdown
    pages = [
        f"<!-- page: {page_no} -->\n\n![page_{page_no}]({image_dir_name}/page_{page_no:04d}.png)"
        for page_no in range(1, total_pages + 1)
    ]
    return markdown.rstrip() + "\n\n" + "\n\n".join(pages)


def _export_pdf_page_images(source_file: str, image_dir: Path, scale: float = _PAGE_IMAGE_SCALE) -> int:
    """Export PDF pages as PNG and return count."""
    import fitz  # type: ignore

    image_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    doc = fitz.open(source_file)
    try:
        for idx, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
            out = image_dir / f"page_{idx:04d}.png"
            pix.save(str(out))
            count += 1
    finally:
        doc.close()
    return count


def _markdown_to_items(markdown: str) -> List[tuple]:
    """Delegate to the public chunker API (app.models.chunker)."""
    return markdown_to_items(markdown)


# Thin wrappers kept for any external callers that imported these directly.
# They delegate to chunker.py.
from app.models.chunker import (  # noqa: E402
    _split_markdown_section,
    _find_markdown_boundary,
)


def _docling_items_to_chunks(doc):
    """Legacy Docling block extraction kept for older local installations."""
    results = []
    for item in doc.iterate_items():
        text = getattr(item, "text", "") or ""
        page = getattr(item, "page_no", 0) or 0
        if text.strip():
            results.append((text.strip(), page, [], ""))
    return results


class IngestWorker(QThread):
    progress = pyqtSignal(int, int)            # (current, total) embed progress
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
            # Generic file import: parser backend is selected manually by config.
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
        image_dir = _converted_image_dir(file_path)

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

        exported_pages = 0
        try:
            exported_pages = _export_pdf_page_images(file_path, image_dir)
        except Exception as e:
            logger.warning(f"Page image export skipped: {e}")

        if exported_pages > 0:
            markdown = _inject_page_images_if_needed(markdown, image_dir.name, exported_pages)

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
        embed = EmbedManager() if index_vectors else None
        chroma = ChromaStore() if index_vectors else None

        chunks: List[dict] = []
        used_ids = set()
        for item in items:
            # items from _markdown_to_items() have 4 elements (text, page, coords, heading_path)
            # items from _parse_pdf() have 4 elements (text, page, coords, heading_level_str)
            text = item[0]
            page = item[1] if len(item) > 1 else 0
            coords = item[2] if len(item) > 2 else []
            heading_path = item[3] if len(item) > 3 else ""
            chunk_texts = [text] if source_type == "markdown" else _rough_split(text)
            for chunk_text in chunk_texts:
                if chunk_text.strip():
                    chunk_id = stable_chunk_id(
                        source_file=self.file_path,
                        source_type=source_type,
                        page=page,
                        heading_path=heading_path,
                        content=chunk_text,
                        used_ids=used_ids,
                    )
                    chunks.append({
                        "id": chunk_id,
                        "content": chunk_text,
                        "page": page,
                        "coords": coords,
                        "heading_path": heading_path,
                        "block_type": "text",
                    })

        total = len(chunks)
        logger.info(f"IngestWorker: {total} {source_type} chunks from {self.file_path}")

        if index_vectors:
            if hasattr(embed, "load") and not getattr(embed, "is_loaded", False):
                if not embed.load():
                    detail = getattr(embed, "last_error", "") or "Check PyTorch / sentence-transformers setup"
                    self.error_occurred.emit(f"Embedding model load failed: {detail}")
                    self.finished.emit(False)
                    return
            self.stage_changed.emit(f"Embedding {total} chunk(s)...")
        else:
            self.stage_changed.emit(f"Converted, exporting {total} chunk(s)...")

        export_rows: List[dict] = []
        for i, chunk in enumerate(chunks):
            try:
                vector_dim = None
                stored = False
                if index_vectors:
                    [vec] = embed.encode([chunk["content"]])
                    vector_dim = len(vec)
                    chroma.add(
                        content=chunk["content"],
                        embedding=vec,
                        source_type=source_type,
                        anchor_id=chunk["id"],
                        pdf_payload={
                            "file": str(Path(self.file_path).name),
                            "page": chunk["page"],
                            "coords": chunk["coords"],
                        } if source_type == "pdf" else {},
                        is_manual=False,
                        doc_id=chunk["id"],
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
                        "vector_dim": vector_dim,
                        "stored": stored,
                        "is_manual": False,
                    }
                )
            except Exception as e:
                logger.error(f"Chunk ingest failed: {e}", exc_info=True)

        if total and not export_rows:
            detail = getattr(embed, "last_error", "") or "Check PyTorch / sentence-transformers setup"
            self.error_occurred.emit(f"Embedding generation failed: {detail}")
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

