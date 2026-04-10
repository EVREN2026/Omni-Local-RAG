"""
IngestWorker 鈥?chunks a document, embeds it and stores into ChromaDB.
Supports manual document parsing (docling/unstructured/mineru/marker/ocr),
plus manual video clip ingestion.
"""

import re
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from app.models.chroma_store import ChromaStore
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
    """Na茂ve whitespace-based splitter (replace with tiktoken for production)."""
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
        raise RuntimeError(f"鏈煡瑙ｆ瀽鍣? {name}")
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
        # All preamble and nothing else 鈥?emit as one chunk
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
        text.rfind(".", lower_bound, end),
        text.rfind(",", lower_bound, end),
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
    progress = pyqtSignal(int, int)            # (current, total) 鈥?embed progress
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

        self.stage_changed.emit(f"杞崲瀹屾垚: {md_path.name}")
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
            self.error_occurred.emit(f"Markdown 璇诲彇澶辫触: {e}")
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

        self.stage_changed.emit("瀵煎叆瀹屾垚")
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

