"""
IngestWorker — chunks a document, embeds it and stores into ChromaDB.
Supports PDF (via Docling + fallback OCR) and manual video clip ingestion.
"""

import os
import re
import uuid
from io import BytesIO
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from app.models.chroma_store import ChromaStore
from app.models.embed_manager import EmbedManager
from app.utils import config as cfg
from app.utils.logger import logger

_MAX_CHUNK_TOKENS = 512
_OVERLAP_TOKENS = 64
_DEFAULT_MARKDOWN_MAX_CHARS = 1800
_DEFAULT_MARKDOWN_OVERLAP_CHARS = 240


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
    parsers = {
        "docling": _parse_pdf_with_docling,
        "marker": _parse_pdf_with_marker,
        "pymupdf": _parse_pdf_with_pymupdf,
        "ocr": _ocr_fallback,
    }
    for name in _pdf_parser_order():
        parser = parsers.get(name)
        if parser is None:
            logger.warning(f"Unknown PDF parser configured: {name}")
            continue
        try:
            results = parser(file_path)
        except Exception as e:
            logger.info(f"PDF parser {name} unavailable ({e})")
            continue
        if results:
            logger.info(f"PDF parsed with {name}: {len(results)} text blocks")
            return results
    return []


def _pdf_parser_order() -> List[str]:
    order = cfg.get("pdf.parser_order", ["docling", "marker", "pymupdf", "ocr"])
    if not isinstance(order, list):
        return ["docling", "marker", "pymupdf", "ocr"]
    normalized = [str(name).strip().lower() for name in order if str(name).strip()]
    return normalized or ["docling", "marker", "pymupdf", "ocr"]


def _parse_pdf_with_pymupdf(file_path: str):
    """Fast, dependency-light text extraction for normal text PDFs."""
    results = []
    try:
        import fitz  # type: ignore

        doc = fitz.open(file_path)
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                results.append((text.strip(), page.number + 1, [], ""))
    except Exception as e:
        logger.warning(f"PyMuPDF text extraction failed ({e})")
    return results


def _parse_pdf_with_docling(file_path: str):
    """Industrial-grade PDF-to-Markdown extraction when Docling is available."""
    from docling.document_converter import DocumentConverter  # type: ignore

    converter = DocumentConverter()
    result = converter.convert(file_path)
    doc = getattr(result, "document", result)
    if hasattr(doc, "export_to_markdown"):
        markdown = doc.export_to_markdown()
        return _markdown_to_items(markdown)
    return _docling_items_to_chunks(doc)


def _parse_pdf_with_marker(file_path: str):
    """High-throughput PDF-to-Markdown extraction when marker-pdf is available."""
    os.environ.setdefault("TORCH_DEVICE", cfg.get("pdf.marker_device", "cpu"))

    from marker.converters.pdf import PdfConverter  # type: ignore
    from marker.models import create_model_dict  # type: ignore
    from marker.output import text_from_rendered  # type: ignore

    converter = PdfConverter(
        artifact_dict=create_model_dict(),
    )
    rendered = converter(file_path)
    markdown, _, _ = text_from_rendered(rendered)
    return _markdown_to_items(markdown)


def _markdown_to_items(markdown: str):
    """Structure-aware Markdown chunking with heading context."""
    results = []
    page = 0
    heading_stack: List[str] = []
    section_blocks: List[str] = []
    section_page = 0
    block_lines: List[str] = []
    in_code_block = False

    max_chars = int(cfg.get("chunking.max_chars", _DEFAULT_MARKDOWN_MAX_CHARS))
    overlap_chars = int(cfg.get("chunking.overlap_chars", _DEFAULT_MARKDOWN_OVERLAP_CHARS))
    keep_heading_path = bool(cfg.get("chunking.keep_heading_path", True))

    def flush_section() -> None:
        nonlocal section_blocks, section_page
        body = "\n\n".join(block.strip() for block in section_blocks if block.strip()).strip()
        if body:
            heading_path = heading_stack if keep_heading_path else []
            for chunk in _split_markdown_section(body, heading_path, max_chars, overlap_chars):
                if chunk.strip():
                    results.append((chunk, section_page, [], ""))
        section_blocks = []
        section_page = page

    def add_block(text: str) -> None:
        nonlocal page, section_page
        stripped = text.strip()
        if not stripped:
            return
        marker_page = _extract_marker_page_number(stripped)
        if marker_page is not None:
            flush_section()
            page = marker_page
            section_page = page
            return
        heading = _parse_markdown_heading(stripped)
        if heading:
            flush_section()
            level, title = heading
            del heading_stack[level - 1 :]
            heading_stack.append(title)
            section_page = page
            rest = "\n".join(stripped.splitlines()[1:]).strip()
            if rest:
                section_blocks.append(rest)
            return
        section_blocks.append(stripped)

    def flush_block() -> None:
        if block_lines:
            add_block("\n".join(block_lines))
            block_lines.clear()

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            block_lines.append(line)
            in_code_block = not in_code_block
            if not in_code_block:
                flush_block()
            continue
        if not stripped and not in_code_block:
            flush_block()
            continue
        block_lines.append(line)

    flush_block()
    flush_section()
    if not results and markdown.strip():
        results.append((markdown.strip(), page, [], ""))
    return results


def _parse_markdown_heading(text: str) -> Optional[tuple]:
    first_line = text.splitlines()[0].strip()
    match = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", first_line)
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


def _split_markdown_section(
    body: str,
    heading_path: List[str],
    max_chars: int,
    overlap_chars: int,
) -> List[str]:
    prefix = ""
    if heading_path:
        prefix = "Heading path: " + " > ".join(heading_path) + "\n\n"
    limit = max(400, max_chars - len(prefix))
    body = body.strip()
    if len(prefix) + len(body) <= max_chars:
        return [prefix + body]

    chunks = []
    start = 0
    while start < len(body):
        end = min(start + limit, len(body))
        if end < len(body):
            boundary = _find_markdown_boundary(body, start, end)
            if boundary > start:
                end = boundary
        part = body[start:end].strip()
        if part:
            chunks.append(prefix + part)
        if end >= len(body):
            break
        next_start = max(start + 1, end - overlap_chars)
        start = next_start
    return chunks


def _find_markdown_boundary(text: str, start: int, end: int) -> int:
    lower_bound = start + int((end - start) * 0.6)
    candidates = [
        text.rfind("\n\n", lower_bound, end),
        text.rfind("\n", lower_bound, end),
        text.rfind("。", lower_bound, end),
        text.rfind("；", lower_bound, end),
        text.rfind(".", lower_bound, end),
        text.rfind(";", lower_bound, end),
        text.rfind(" ", lower_bound, end),
    ]
    return max(candidates)


def _extract_marker_page_number(line: str) -> Optional[int]:
    if not line.isdigit():
        return None
    page = int(line)
    return page + 1 if page == 0 else page


def _docling_items_to_chunks(doc):
    """Legacy Docling block extraction kept for older local installations."""
    results = []
    for item in doc.iterate_items():
        text = getattr(item, "text", "") or ""
        page = getattr(item, "page_no", 0) or 0
        if text.strip():
            results.append((text.strip(), page, [], ""))
    return results


def _ocr_fallback(file_path: str):
    """OCR fallback for scanned PDFs when text extraction returns nothing."""
    results = []
    try:
        import fitz  # type: ignore
        from PIL import Image  # type: ignore
        import pytesseract  # type: ignore

        doc = fitz.open(file_path)
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.open(BytesIO(pix.tobytes("png")))
            text = pytesseract.image_to_string(image, lang="chi_sim+eng")
            if text.strip():
                results.append((text.strip(), page.number + 1, [], ""))
    except Exception as e:
        logger.error(f"OCR fallback failed: {e}")
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
        elif suffix in {".md", ".markdown"}:
            self._ingest_markdown()
        else:
            self.error_occurred.emit(f"不支持的文件类型: {suffix}")
            self.finished.emit(False)

    def _ingest_pdf(self) -> None:
        try:
            items = _parse_pdf(self.file_path)
        except Exception as e:
            self.error_occurred.emit(str(e))
            self.finished.emit(False)
            return

        if not items:
            self.error_occurred.emit("PDF 未提取到可索引文本，请确认不是纯图片扫描件，或安装 Tesseract OCR")
            self.finished.emit(False)
            return

        self._ingest_items(items, source_type="pdf")

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
            self.error_occurred.emit("Markdown 文件没有可索引内容")
            self.finished.emit(False)
            return

        self._ingest_items(items, source_type="markdown")

    def _ingest_items(self, items: List[tuple], source_type: str) -> None:
        embed = EmbedManager()
        chroma = ChromaStore()

        chunks: List[dict] = []
        for text, page, coords, _ in items:
            chunk_texts = [text] if source_type == "markdown" else _rough_split(text)
            for chunk_text in chunk_texts:
                if chunk_text.strip():
                    chunks.append({
                        "id": str(uuid.uuid4()),
                        "content": chunk_text,
                        "page": page,
                        "coords": coords,
                    })

        total = len(chunks)
        logger.info(f"IngestWorker: {total} {source_type} chunks from {self.file_path}")

        if hasattr(embed, "load") and not getattr(embed, "is_loaded", False):
            if not embed.load():
                detail = getattr(embed, "last_error", "") or "请检查 PyTorch / sentence-transformers 环境"
                self.error_occurred.emit(f"Embedding 模型加载失败：{detail}")
                self.finished.emit(False)
                return

        export_rows: List[dict] = []
        for i, chunk in enumerate(chunks):
            try:
                [vec] = embed.encode([chunk["content"]])
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
                self.progress.emit(i + 1, total)
                export_rows.append(
                    {
                        "chunk_id": chunk["id"],
                        "anchor_id": chunk["id"],
                        "source_type": source_type,
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

        if total and not export_rows:
            detail = getattr(embed, "last_error", "") or "请检查 PyTorch / sentence-transformers 环境"
            self.error_occurred.emit(f"Embedding 生成失败：{detail}")
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
