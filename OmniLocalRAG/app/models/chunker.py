"""
chunker.py — Public API for structure-aware Markdown chunking.

Extracted from workers/ingest_worker.py so that views (ChunkWorkbench,
DataflowPanel) can import chunking functions without crossing the
View → Worker boundary.

Public surface:
  markdown_to_items(markdown, **kwargs) -> List[tuple]
      Returns [(text, page, coords, heading_path_str), ...].

  split_markdown_section(body, heading_path, max_chars, overlap_chars) -> List[str]

All private helpers (_parse_markdown_heading, _find_markdown_boundary, etc.)
remain here as module-level functions so ingest_worker.py can delegate to them.
"""

import re
from typing import List, Optional

from app.utils import config as cfg

_DEFAULT_MAX_CHARS = 1800
_DEFAULT_OVERLAP_CHARS = 240
_DEFAULT_MIN_SECTION_CHARS = 80


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def markdown_to_items(
    markdown: str,
    max_chars: Optional[int] = None,
    overlap_chars: Optional[int] = None,
    keep_heading_path: Optional[bool] = None,
    min_section_chars: Optional[int] = None,
) -> List[tuple]:
    """Structure-aware Markdown → chunk list.

    Each element: (text: str, page: int, coords: list, heading_path_str: str)

    Parameters default to config.json values when not supplied.
    """
    _max_chars = max_chars if max_chars is not None else int(
        cfg.get("chunking.max_chars", _DEFAULT_MAX_CHARS)
    )
    _overlap = overlap_chars if overlap_chars is not None else int(
        cfg.get("chunking.overlap_chars", _DEFAULT_OVERLAP_CHARS)
    )
    _keep_hp = keep_heading_path if keep_heading_path is not None else bool(
        cfg.get("chunking.keep_heading_path", True)
    )
    _min_sec = min_section_chars if min_section_chars is not None else int(
        cfg.get("chunking.min_section_chars", _DEFAULT_MIN_SECTION_CHARS)
    )
    return _markdown_to_items(markdown, _max_chars, _overlap, _keep_hp, _min_sec)


def split_markdown_section(
    body: str,
    heading_path: List[str],
    max_chars: int,
    overlap_chars: int,
) -> List[str]:
    """Split one Markdown section body into overlap-chunked strings."""
    return _split_markdown_section(body, heading_path, max_chars, overlap_chars)


# ---------------------------------------------------------------------------
# Internal implementation (also importable for backwards compat)
# ---------------------------------------------------------------------------

def _markdown_to_items(
    markdown: str,
    max_chars: int = _DEFAULT_MAX_CHARS,
    overlap_chars: int = _DEFAULT_OVERLAP_CHARS,
    keep_heading_path: bool = True,
    min_section_chars: int = _DEFAULT_MIN_SECTION_CHARS,
) -> List[tuple]:
    """Implementation — prefer calling ``markdown_to_items()`` instead."""
    results: List[tuple] = []
    page = 0
    heading_stack: List[tuple] = []   # list of (level, title)
    section_blocks: List[str] = []
    section_page = 0
    block_lines: List[str] = []
    in_code_block = False

    def _current_heading_path() -> List[str]:
        return [title for _, title in heading_stack]

    def flush_section() -> None:
        nonlocal section_blocks, section_page
        body = "\n\n".join(b.strip() for b in section_blocks if b.strip()).strip()
        if body and len(body) >= min_section_chars:
            hp = _current_heading_path() if keep_heading_path else []
            hp_str = " > ".join(hp) if hp else ""
            for chunk in _split_markdown_section(body, hp, max_chars, overlap_chars):
                if chunk.strip():
                    results.append((chunk, section_page, [], hp_str))
        elif body:
            hp = _current_heading_path() if keep_heading_path else []
            hp_str = " > ".join(hp) if hp else ""
            prefix = ("Heading path: " + hp_str + "\n\n") if hp_str else ""
            results.append((prefix + body, section_page, [], hp_str))
        section_blocks.clear()
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
            heading_stack[:] = [(l, t) for l, t in heading_stack if l < level]
            heading_stack.append((level, title))
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

    # Post-process: merge tiny preamble chunks (before any real heading)
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
        start = max(start + 1, end - overlap_chars)
    return chunks


def _find_markdown_boundary(text: str, start: int, end: int) -> int:
    lower_bound = start + int((end - start) * 0.6)
    candidates = [
        text.rfind("\n\n", lower_bound, end),
        text.rfind("\n",   lower_bound, end),
        text.rfind(".",    lower_bound, end),
        text.rfind(",",    lower_bound, end),
        text.rfind(";",    lower_bound, end),
        text.rfind(" ",    lower_bound, end),
    ]
    return max(candidates)


def _extract_marker_page_number(line: str) -> Optional[int]:
    if not line.isdigit():
        return None
    page = int(line)
    return page + 1 if page == 0 else page
