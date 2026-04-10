"""
IngestWorker — chunks a document, embeds it and stores into ChromaDB.
Supports manual document parsing (docling/unstructured/mineru/marker/ocr),
plus manual video clip ingestion.
"""

import re
import uuid
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from app.models.chroma_store import ChromaStore
from app.models.embed_manager import EmbedManager
from app.parsers.document_parsers import PARSER_REGISTRY
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


def _markdown_to_items(markdown: str):
    """Structure-aware Markdown chunking with heading context.

    Design goals:
    - Headings are NEVER emitted as standalone chunks; they become the prefix
      of whichever body block follows them.
    - Very short lines (metadata, author, date, version, lone TOC entries) are
      accumulated into a single "preamble" chunk instead of generating dozens
      of one-liner chunks.
    - Each emitted chunk carries (text, page, coords, heading_path_str) so
      callers can record heading_path as metadata.
    - Tables remain as their HTML/Markdown text; they are included in the
      surrounding section body (not stripped).
    """
    results: List[tuple] = []
    page = 0
    heading_stack: List[tuple] = []   # list of (level, title)
    section_blocks: List[str] = []
    section_page = 0
    block_lines: List[str] = []
    in_code_block = False

    max_chars = int(cfg.get("chunking.max_chars", _DEFAULT_MARKDOWN_MAX_CHARS))
    overlap_chars = int(cfg.get("chunking.overlap_chars", _DEFAULT_MARKDOWN_OVERLAP_CHARS))
    keep_heading_path = bool(cfg.get("chunking.keep_heading_path", True))
    # Minimum body length (chars) for a section to be emitted as its own chunk.
    min_section_chars = int(cfg.get("chunking.min_section_chars", 80))

    def _current_heading_path() -> List[str]:
        return [title for _, title in heading_stack]

    def flush_section() -> None:
        nonlocal section_blocks, section_page
        body = "\n\n".join(block.strip() for block in section_blocks if block.strip()).strip()
        if body and len(body) >= min_section_chars:
            heading_path = _current_heading_path() if keep_heading_path else []
            heading_path_str = " > ".join(heading_path) if heading_path else ""
            for chunk in _split_markdown_section(body, heading_path, max_chars, overlap_chars):
                if chunk.strip():
                    results.append((chunk, section_page, [], heading_path_str))
        elif body:
            # Short section: append to previous chunk's body or buffer for next
            # section by leaving it in section_blocks for the caller to merge.
            # Strategy: just emit it with heading context so it's not lost.
            heading_path = _current_heading_path() if keep_heading_path else []
            heading_path_str = " > ".join(heading_path) if heading_path else ""
            prefix = ("Heading path: " + heading_path_str + "\n\n") if heading_path_str else ""
            results.append((prefix + body, section_page, [], heading_path_str))
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
            # Flush the *previous* section before starting a new one.
            flush_section()
            level, title = heading
            # Trim heading_stack to this level.
            heading_stack[:] = [(l, t) for l, t in heading_stack if l < level]
            heading_stack.append((level, title))
            section_page = page
            # Any inline content after the heading marker goes into the new section.
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
        stripped_line = line.strip()
        if stripped_line.startswith("```") or stripped_line.startswith("~~~"):
            block_lines.append(line)
            in_code_block = not in_code_block
            if not in_code_block:
                flush_block()
            continue
        if not stripped_line and not in_code_block:
            flush_block()
            continue
        block_lines.append(line)

    flush_block()
    flush_section()

    # --- post-process: merge tiny consecutive preamble chunks ---
    # Chunks shorter than min_section_chars that appear at the very beginning
    # (before any real heading) are merged into the first substantial chunk.
    final: List[tuple] = []
    preamble_buf: List[str] = []
    for item in results:
        text, pg, coords, hp = item
        if not hp and len(text) < min_section_chars * 2:
            preamble_buf.append(text)
        else:
            if preamble_buf:
                merged = "\n\n".join(preamble_buf)
                preamble_buf = []
                text = merged + "\n\n" + text
            final.append((text, pg, coords, hp))
    if preamble_buf:
        # All preamble and nothing else — emit as one chunk
        final.append(("\n\n".join(preamble_buf), page, [], ""))

    if not final and markdown.strip():
        final.append((markdown.strip(), page, [], ""))
    return final


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


class IngestWorker(QThread):
    progress = pyqtSignal(int, int)            # (current, total) — embed progress
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
            items = self._parse_pdf_with_signals(self.file_path)
        except Exception as e:
            self.error_occurred.emit(str(e))
            self.finished.emit(False)
            return

        if not items:
            self.error_occurred.emit("未提取到可索引文本，请检查所选解析器是否支持该文件格式")
            self.finished.emit(False)
            return

        # Product decision: PDF import defaults to "convert/export only" so users
        # can first review/correct chunks before any vectorisation.
        index_vectors = bool(cfg.get("pdf.index_on_import", False))
        self._ingest_items(items, source_type="pdf", index_vectors=index_vectors)

    def _parse_pdf_with_signals(self, file_path: str) -> list:
        """Parse with manually selected parser only (no auto fallback)."""
        parser_name = _selected_parser_name()
        parser = PARSER_REGISTRY.get(parser_name)
        if parser is None:
            raise RuntimeError(f"未知解析器: {parser_name}")

        # Emit total page count once (requires fitz; skip silently if unavailable)
        try:
            import fitz  # type: ignore
            _doc = fitz.open(file_path)
            total_pages = len(_doc)
            _doc.close()
            self.page_progress.emit(0, total_pages)
        except Exception:
            total_pages = 0

        self.stage_changed.emit(f"正在用 {parser_name} 解析文件…")
        results = parser.parse(file_path)
        if results:
            logger.info(f"PDF parsed with {parser_name}: {len(results)} text blocks")
            if total_pages:
                self.page_progress.emit(total_pages, total_pages)
            self.parse_done.emit(parser_name, len(results))
        return results

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

    def _ingest_items(self, items: List[tuple], source_type: str, index_vectors: bool = True) -> None:
        self.stage_changed.emit("正在切块…")
        embed = EmbedManager() if index_vectors else None
        chroma = ChromaStore() if index_vectors else None

        chunks: List[dict] = []
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
                    chunks.append({
                        "id": str(uuid.uuid4()),
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
                    detail = getattr(embed, "last_error", "") or "请检查 PyTorch / sentence-transformers 环境"
                    self.error_occurred.emit(f"Embedding 模型加载失败：{detail}")
                    self.finished.emit(False)
                    return
            self.stage_changed.emit(f"正在向量化 {total} 个切块…")
        else:
            self.stage_changed.emit(f"转换完成，正在导出 {total} 个切块…")

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
