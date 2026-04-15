"""
Structure-aware Markdown chunking for technical RAG.

This module keeps compatibility with the legacy tuple-style chunk contract while
adding richer semantic metadata for technical documentation:
- heading path extraction
- block typing (text / term / tutorial / image / table / parameter_table / code)
- table row description enhancement
- image context extraction from nearby lines
- separate display_content and embedding_text

Degradation strategy for documents without heading structure:
- No headings at all  → paragraph-window chunking (sliding window over paragraphs)
- Only top-level heading (single depth) → treat as flat section, merge micro-chunks
- Mixed PDF-extracted Unicode variants → NFKC normalisation on heading_path
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.utils import config as cfg

_DEFAULT_MAX_CHARS = 1800
_DEFAULT_OVERLAP_CHARS = 240
_DEFAULT_MIN_SECTION_CHARS = 80
_DEFAULT_IMAGE_CONTEXT_LINES = 2

# Minimum chars a term/text chunk must have before being merged with neighbours.
# UV source doc analysis shows single-sentence term chunks under each H4 step
# heading reaching up to ~95 chars ("那么,如果因为物体移动太快...这时就需要增益").
# Setting threshold to 100 captures those while leaving normal paragraphs intact.
_DEFAULT_MICRO_CHUNK_CHARS = 100

_PAGE_MARKER_RE = re.compile(r"^<!--\s*page:\s*(\d+)\s*-->$", re.IGNORECASE)
_MD_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*(?:\s*:?-{3,}:?\s*)?\|?\s*$")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_LIST_LINE_RE = re.compile(
    r"^\s*(?:[-*+]\s+|\d+[.)]\s+|第\s*\d+\s*步[:：]?\s+|步骤\s*\d+[:：]?\s+|step\s*\d+[:：]?\s+)",
    re.IGNORECASE,
)
_STEP_TEXT_RE = re.compile(r"(?:第\s*\d+\s*步|步骤\s*\d+|step\s*\d+)", re.IGNORECASE)
_PARAMETER_HINT_RE = re.compile(
    r"(参数|配置|默认|单位|说明|含义|取值|名称|value|default|parameter|option)",
    re.IGNORECASE,
)

# Detect known "document is flat" heading titles to skip indexing as nav chunks.
_TOC_TITLE_RE = re.compile(
    r"^(目录|目录导航|table\s+of\s+contents?|contents?|index|索引)$",
    re.IGNORECASE,
)

# --- Unicode normalisation helpers -------------------------------------------

# Supplementary mapping for CJK Radical characters (U+2E80–U+2EFF) and
# specific CJK Unified Ideograph variants that NFKC does NOT normalise
# but which appear frequently in PDF-extracted Chinese text as OCR artefacts.
#
# Source: observed in UV.chunks.md heading_path values produced by PDF parsers.
# Key: problematic character, Value: canonical replacement.
_CJK_RADICAL_MAP: Dict[str, str] = {
    "\u2EC6": "\u89D2",  # ⻆ CJK RADICAL SIMPLIFIED HORN → 角
    "\u2ED4": "\u95E8",  # ⻔ CJK RADICAL C-SIMPLIFIED GATE → 门
    "\u3B35": "\u80F6",  # 㬵 CJK UNIFIED IDEOGRAPH-3B35 → 胶 (glue/adhesive)
}


# Regex to remove spurious ASCII spaces inserted between CJK characters by
# PDF-to-Markdown parsers (e.g. "快速⼊ ⻔" from a PDF line-break becoming a space).
# Only removes space *between* two CJK Unified Ideograph characters — never inside
# ASCII tokens, numbers, or Latin text.
_CJK_SPACE_RE = re.compile(
    r"(?<=[\u4e00-\u9fff\u3400-\u4dbf])[ \t]+(?=[\u4e00-\u9fff\u3400-\u4dbf])"
)


def _nfkc(text: str) -> str:
    """Normalise PDF-extracted text:
    1. NFKC — handles Kangxi Radicals (⼊→入, ⽹→网) and compatibility chars.
    2. _CJK_RADICAL_MAP — targeted fixes for radicals NFKC leaves unchanged
       (⻔→门, ⻆→角) and specific OCR ideograph substitutions (㬵→胶).
    3. _CJK_SPACE_RE — removes spurious intra-CJK spaces produced when PDF
       parsers convert line-breaks inside Chinese words into ASCII spaces
       (e.g. "快速入 门" → "快速入门").
    """
    normalised = unicodedata.normalize("NFKC", text or "")
    if any(c in normalised for c in _CJK_RADICAL_MAP):
        for src, dst in _CJK_RADICAL_MAP.items():
            normalised = normalised.replace(src, dst)
    normalised = _CJK_SPACE_RE.sub("", normalised)
    return normalised


@dataclass
class ChunkItem:
    content: str
    page: int = 0
    coords: List[Any] = field(default_factory=list)
    heading_path: str = ""
    block_type: str = "text"
    metadata: Dict[str, Any] = field(default_factory=dict)
    display_content: str = ""
    embedding_text: str = ""

    def __post_init__(self) -> None:
        if not self.display_content:
            self.display_content = self.content
        if not self.embedding_text:
            self.embedding_text = self.display_content or self.content

    @property
    def legacy_text(self) -> str:
        return self.embedding_text or self.display_content or self.content

    def __iter__(self):
        yield self.legacy_text
        yield self.page
        yield self.coords
        yield self.heading_path

    def __getitem__(self, index: int):
        return (self.legacy_text, self.page, self.coords, self.heading_path)[index]

    def __len__(self) -> int:
        return 4


@dataclass
class _Section:
    page: int
    heading_path: List[str]
    lines: List[str] = field(default_factory=list)


@dataclass
class _Block:
    kind: str
    lines: List[str]
    line_index: int = 0


def markdown_to_items(
    markdown: str,
    max_chars: Optional[int] = None,
    overlap_chars: Optional[int] = None,
    keep_heading_path: Optional[bool] = None,
    min_section_chars: Optional[int] = None,
) -> List[ChunkItem]:
    _max_chars = max_chars if max_chars is not None else int(cfg.get("chunking.max_chars", _DEFAULT_MAX_CHARS))
    _overlap = overlap_chars if overlap_chars is not None else int(cfg.get("chunking.overlap_chars", _DEFAULT_OVERLAP_CHARS))
    _keep_path = keep_heading_path if keep_heading_path is not None else bool(cfg.get("chunking.keep_heading_path", True))
    _min_chars = min_section_chars if min_section_chars is not None else int(cfg.get("chunking.min_section_chars", _DEFAULT_MIN_SECTION_CHARS))
    _micro = int(cfg.get("chunking.micro_chunk_chars", _DEFAULT_MICRO_CHUNK_CHARS))
    return _markdown_to_items(markdown, _max_chars, _overlap, _keep_path, _min_chars, _micro)


def split_markdown_section(
    body: str,
    heading_path: List[str],
    max_chars: int,
    overlap_chars: int,
) -> List[str]:
    return _split_markdown_section(body, heading_path, max_chars, overlap_chars)


def coerce_chunk_item(item: Any) -> ChunkItem:
    if isinstance(item, ChunkItem):
        return item
    if isinstance(item, dict):
        return ChunkItem(
            content=str(item.get("content", "")),
            page=int(item.get("page", 0) or 0),
            coords=list(item.get("coords", []) or []),
            heading_path=str(item.get("heading_path", "") or ""),
            block_type=str(item.get("block_type", "text") or "text"),
            metadata=dict(item.get("metadata", {}) or {}),
            display_content=str(item.get("display_content", item.get("content", "")) or ""),
            embedding_text=str(item.get("embedding_text", item.get("content", "")) or ""),
        )
    if isinstance(item, (tuple, list)):
        text = str(item[0]) if len(item) > 0 else ""
        page = int(item[1] or 0) if len(item) > 1 else 0
        coords = list(item[2] or []) if len(item) > 2 else []
        heading_path = str(item[3] or "") if len(item) > 3 else ""
        return ChunkItem(
            content=text,
            page=page,
            coords=coords,
            heading_path=heading_path,
            block_type="text",
            metadata={"path": heading_path, "type": "text"},
            display_content=text,
            embedding_text=text,
        )
    raise TypeError(f"Unsupported chunk item type: {type(item)!r}")


def _markdown_to_items(
    markdown: str,
    max_chars: int = _DEFAULT_MAX_CHARS,
    overlap_chars: int = _DEFAULT_OVERLAP_CHARS,
    keep_heading_path: bool = True,
    min_section_chars: int = _DEFAULT_MIN_SECTION_CHARS,
    micro_chunk_chars: int = _DEFAULT_MICRO_CHUNK_CHARS,
) -> List[ChunkItem]:
    markdown = str(markdown or "")
    image_context_lines = int(cfg.get("chunking.image_context_lines", _DEFAULT_IMAGE_CONTEXT_LINES))
    sections = _parse_sections(markdown)

    # -- Detect document structure type ---------------------------------------
    # flat_document: no heading hierarchy (depth <= 1 across all sections)
    max_heading_depth = max((len(s.heading_path) for s in sections), default=0)
    is_flat_document = max_heading_depth <= 1

    items: List[ChunkItem] = []

    for section in sections:
        items.extend(
            _section_to_items(
                section=section,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
                keep_heading_path=keep_heading_path,
                min_section_chars=min_section_chars,
                image_context_lines=image_context_lines,
            )
        )

    items = _merge_small_preamble(items, min_section_chars)

    # -- Post-processing: merge micro-chunks within the same heading ----------
    # Handles UV-doc patterns like:
    #   chunk33: "速度和双工 值:1.0Gbps全双工"   (11 chars)
    #   chunk34: "巨帧数据包 值:9014字节"          (10 chars)
    #   chunk35: "接收缓冲区 值:2048"             (9 chars)
    # These should be a single parameter-set chunk.
    items = _merge_micro_chunks(items, micro_chunk_chars, max_chars)

    # -- For flat documents: sliding-window paragraph grouping ---------------
    # When a document has no heading hierarchy (e.g. plain prose, FAQ, log),
    # merge adjacent text blocks into window-sized chunks with overlap.
    if is_flat_document and items:
        items = _apply_flat_document_windowing(items, max_chars, overlap_chars)

    if items:
        return items

    fallback = markdown.strip()
    if not fallback:
        return []
    metadata = {"path": "", "type": "text", "title": ""}
    return [
        ChunkItem(
            content=fallback,
            page=0,
            coords=[],
            heading_path="",
            block_type="text",
            metadata=metadata,
            display_content=fallback,
            embedding_text=_build_embedding_text(fallback, "", "text", metadata),
        )
    ]


def _parse_sections(markdown: str) -> List[_Section]:
    sections: List[_Section] = []
    heading_stack: List[Tuple[int, str]] = []
    body_lines: List[str] = []
    page = 0
    section_page = 0
    in_code_block = False

    def current_path() -> List[str]:
        return [title for _, title in heading_stack]

    def flush_section() -> None:
        nonlocal body_lines, section_page
        text = "\n".join(body_lines).strip("\n")
        if text.strip():
            sections.append(_Section(page=section_page, heading_path=current_path(), lines=text.splitlines()))
        body_lines = []
        section_page = page

    for line in markdown.splitlines():
        stripped = line.strip()
        marker_page = _extract_marker_page_number(stripped)
        if marker_page is not None and not in_code_block:
            flush_section()
            page = marker_page
            section_page = page
            continue

        if not in_code_block and stripped:
            heading = _parse_markdown_heading(stripped)
            if heading:
                flush_section()
                level, title = heading
                heading_stack[:] = [(l, t) for l, t in heading_stack if l < level]
                heading_stack.append((level, title))
                section_page = page
                continue

        body_lines.append(line)
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_block = not in_code_block

    flush_section()
    return sections


def _section_to_items(
    section: _Section,
    max_chars: int,
    overlap_chars: int,
    keep_heading_path: bool,
    min_section_chars: int,
    image_context_lines: int,
) -> List[ChunkItem]:
    heading_path_list = section.heading_path if keep_heading_path else []
    # Apply NFKC normalisation to each heading segment to collapse PDF-extracted
    # Unicode radical/variant characters (⼊→入, ⻔→门, 㬵→胶, etc.)
    heading_path_list = [_nfkc(h) for h in heading_path_list]
    heading_path = " > ".join(heading_path_list)
    title = heading_path_list[-1] if heading_path_list else ""

    # Skip pure table-of-contents sections: they list chapter titles but contain
    # no answerable knowledge. Querying them wastes a top-k slot.
    if title and _TOC_TITLE_RE.match(title.strip()):
        return []

    blocks = _extract_semantic_blocks(section.lines)
    items: List[ChunkItem] = []

    for block in blocks:
        if block.kind == "table":
            items.extend(_build_table_items(block.lines, section.page, heading_path, title))
            continue
        if block.kind == "image":
            items.extend(
                _build_image_items(
                    lines=block.lines,
                    full_section_lines=section.lines,
                    line_index=block.line_index,
                    page=section.page,
                    heading_path=heading_path,
                    title=title,
                    context_lines=image_context_lines,
                )
            )
            continue

        text = "\n".join(block.lines).strip()
        if not text:
            continue
        if not heading_path and len(text) < min_section_chars and block.kind == "text":
            # Merge tiny preambles later instead of producing noisy micro-chunks.
            pass
        block_type = "code" if block.kind == "code" else _classify_text_block(text, title)
        items.extend(
            _build_text_items(
                text=text,
                page=section.page,
                heading_path=heading_path,
                title=title,
                block_type=block_type,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
        )
    return items


def _extract_semantic_blocks(lines: List[str]) -> List[_Block]:
    blocks: List[_Block] = []
    paragraph: List[str] = []
    paragraph_start = 0
    i = 0

    def flush_paragraph() -> None:
        nonlocal paragraph, paragraph_start
        text = "\n".join(paragraph).strip()
        if text:
            blocks.append(_Block(kind="text", lines=text.splitlines(), line_index=paragraph_start))
        paragraph = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            i += 1
            continue

        if _is_html_table_start(stripped):
            flush_paragraph()
            table_lines = [line]
            start_index = i
            i += 1
            while i < len(lines):
                table_lines.append(lines[i])
                if "</table>" in lines[i].lower():
                    i += 1
                    break
                i += 1
            blocks.append(_Block(kind="table", lines=table_lines, line_index=start_index))
            continue

        if _is_markdown_table_start(lines, i):
            flush_paragraph()
            start_index = i
            table_lines = [lines[i], lines[i + 1]]
            i += 2
            while i < len(lines) and _looks_like_table_row(lines[i]):
                table_lines.append(lines[i])
                i += 1
            blocks.append(_Block(kind="table", lines=table_lines, line_index=start_index))
            continue

        if _contains_image(line):
            flush_paragraph()
            blocks.append(_Block(kind="image", lines=[line], line_index=i))
            i += 1
            continue

        if stripped.startswith("```") or stripped.startswith("~~~"):
            flush_paragraph()
            start_index = i
            fence = stripped[:3]
            code_lines = [line]
            i += 1
            while i < len(lines):
                code_lines.append(lines[i])
                if lines[i].strip().startswith(fence):
                    i += 1
                    break
                i += 1
            blocks.append(_Block(kind="code", lines=code_lines, line_index=start_index))
            continue

        if not paragraph:
            paragraph_start = i
        paragraph.append(line)
        i += 1

    flush_paragraph()
    return blocks


def _build_text_items(
    text: str,
    page: int,
    heading_path: str,
    title: str,
    block_type: str,
    max_chars: int,
    overlap_chars: int,
) -> List[ChunkItem]:
    chunks = _split_markdown_section(text, heading_path.split(" > ") if heading_path else [], max_chars, overlap_chars)
    items: List[ChunkItem] = []
    for part in chunks:
        display = _strip_legacy_heading_prefix(part).strip()
        if not display:
            continue
        metadata: Dict[str, Any] = {
            "path": heading_path,
            "type": block_type,
            "title": title,
            "has_table": False,
            "has_image": False,
        }
        if block_type == "term":
            metadata["semantic_description"] = _build_term_description(display, title, heading_path)
        elif block_type == "tutorial":
            metadata["semantic_description"] = _build_tutorial_description(display, title, heading_path)
        elif block_type == "code":
            label = heading_path or title or "未命名章节"
            metadata["semantic_description"] = f"代码或命令片段，位于【{label}】。"
        items.append(
            ChunkItem(
                content=display,
                page=page,
                coords=[],
                heading_path=heading_path,
                block_type=block_type,
                metadata=metadata,
                display_content=display,
                embedding_text=_build_embedding_text(display, heading_path, block_type, metadata),
            )
        )
    return items


def _build_table_items(lines: List[str], page: int, heading_path: str, title: str) -> List[ChunkItem]:
    display = "\n".join(lines).strip()
    description, is_parameter_table = _describe_table(lines, heading_path)
    block_type = "parameter_table" if is_parameter_table else "table"
    metadata = {
        "path": heading_path,
        "type": block_type,
        "title": title,
        "has_table": True,
        "has_image": False,
        "semantic_description": description,
    }
    return [
        ChunkItem(
            content=display,
            page=page,
            coords=[],
            heading_path=heading_path,
            block_type=block_type,
            metadata=metadata,
            display_content=display,
            embedding_text=_build_embedding_text(display, heading_path, block_type, metadata),
        )
    ]


def _build_image_items(
    lines: List[str],
    full_section_lines: List[str],
    line_index: int,
    page: int,
    heading_path: str,
    title: str,
    context_lines: int,
) -> List[ChunkItem]:
    raw_line = "\n".join(lines).strip()
    before, after = _neighbor_context(full_section_lines, line_index, context_lines)
    items: List[ChunkItem] = []

    for match in _MD_IMAGE_RE.finditer(raw_line):
        alt = match.group("alt").strip()
        src = match.group("src").strip()
        display_parts: List[str] = []
        if before:
            display_parts.append("\n".join(before))
        display_parts.append(match.group(0))
        if after:
            display_parts.append("\n".join(after))
        display = "\n\n".join(part for part in display_parts if part).strip()
        metadata = {
            "path": heading_path,
            "type": "image",
            "title": title,
            "has_table": False,
            "has_image": True,
            "image_alt": alt,
            "image_path": src,
            "context_before": before,
            "context_after": after,
            "semantic_description": _build_image_description(alt, src, before, after, heading_path),
        }
        items.append(
            ChunkItem(
                content=display or match.group(0),
                page=page,
                coords=[],
                heading_path=heading_path,
                block_type="image",
                metadata=metadata,
                display_content=display or match.group(0),
                embedding_text=_build_embedding_text(display or match.group(0), heading_path, "image", metadata),
            )
        )

    if items:
        return items

    metadata = {
        "path": heading_path,
        "type": "image",
        "title": title,
        "has_table": False,
        "has_image": True,
    }
    return [
        ChunkItem(
            content=raw_line,
            page=page,
            coords=[],
            heading_path=heading_path,
            block_type="image",
            metadata=metadata,
            display_content=raw_line,
            embedding_text=_build_embedding_text(raw_line, heading_path, "image", metadata),
        )
    ]


def _classify_text_block(text: str, title: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "text"
    if lines[0].startswith(("```", "~~~")):
        return "code"
    if sum(1 for line in lines if _LIST_LINE_RE.match(line)) >= 2 or _STEP_TEXT_RE.search(text):
        return "tutorial"
    plain = " ".join(lines)
    if len(plain) <= 220 and len(lines) <= 3 and (title or "：" in plain or ":" in plain):
        return "term"
    return "text"


def _build_term_description(text: str, title: str, heading_path: str) -> str:
    first_sentence = re.split(r"[。；;\n]", text, maxsplit=1)[0].strip()
    label = title or (heading_path.split(" > ")[-1] if heading_path else "术语")
    if not first_sentence:
        return f"术语块，位于【{heading_path or label}】。"
    return f"术语【{label}】的定义或说明：{first_sentence}"


def _build_tutorial_description(text: str, title: str, heading_path: str) -> str:
    label = title or (heading_path.split(" > ")[-1] if heading_path else "操作步骤")
    step_count = len(
        re.findall(
            r"(?:^\s*\d+[.)]\s+|第\s*\d+\s*步|步骤\s*\d+|step\s*\d+)",
            text,
            re.MULTILINE | re.IGNORECASE,
        )
    )
    if step_count <= 0:
        step_count = len([line for line in text.splitlines() if line.strip()])
    return f"教程/操作步骤块，主题为【{label}】，共约 {step_count} 个步骤。"


def _describe_table(lines: List[str], heading_path: str) -> Tuple[str, bool]:
    markdown_rows = _parse_markdown_table(lines)
    html_rows = _parse_html_table(lines) if not markdown_rows else []
    rows = markdown_rows or html_rows
    label = heading_path or "未命名章节"
    if not rows:
        return f"该表格位于【{label}】。", False

    headers = rows[0]
    data_rows = rows[1:] if len(rows) > 1 else []
    is_parameter_table = any(_PARAMETER_HINT_RE.search(str(cell or "")) for cell in headers)

    descriptions = [f"该表格位于【{label}】。"]
    if headers:
        header_text = "、".join(cell for cell in headers if cell)
        if header_text:
            descriptions.append(f"表头包含：{header_text}。")

    for row in data_rows[:24]:
        pairs: List[str] = []
        for idx, cell in enumerate(row):
            cell_text = str(cell or "").strip()
            if not cell_text:
                continue
            header = headers[idx] if idx < len(headers) and headers[idx] else f"列{idx + 1}"
            pairs.append(f"{header}为{cell_text}")
        if pairs:
            descriptions.append("，".join(pairs) + "。")

    return "\n".join(descriptions).strip(), is_parameter_table


def _parse_markdown_table(lines: List[str]) -> List[List[str]]:
    if len(lines) < 2 or not _looks_like_table_row(lines[0]) or not _TABLE_SEPARATOR_RE.match(lines[1].strip()):
        return []
    rows = [_split_table_row(lines[0])]
    for line in lines[2:]:
        if not _looks_like_table_row(line):
            break
        rows.append(_split_table_row(line))
    return rows


def _parse_html_table(lines: List[str]) -> List[List[str]]:
    html = "\n".join(lines)
    row_matches = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.IGNORECASE | re.DOTALL)
    rows: List[List[str]] = []
    for row_html in row_matches:
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row_html, re.IGNORECASE | re.DOTALL)
        clean_cells = [_clean_html_text(cell) for cell in cells]
        if any(clean_cells):
            rows.append(clean_cells)
    return rows


def _split_table_row(line: str) -> List[str]:
    raw = line.strip().strip("|")
    return [cell.strip() for cell in raw.split("|")]


def _clean_html_text(text: str) -> str:
    stripped = _HTML_TAG_RE.sub(" ", text or "")
    return re.sub(r"\s+", " ", stripped).strip()


def _neighbor_context(lines: List[str], line_index: int, context_lines: int) -> Tuple[List[str], List[str]]:
    before: List[str] = []
    after: List[str] = []

    i = line_index - 1
    while i >= 0 and len(before) < context_lines:
        candidate = _clean_context_line(lines[i])
        if candidate:
            before.insert(0, candidate)
        i -= 1

    i = line_index + 1
    while i < len(lines) and len(after) < context_lines:
        candidate = _clean_context_line(lines[i])
        if candidate:
            after.append(candidate)
        i += 1

    return before, after


def _clean_context_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    if _contains_image(stripped):
        return ""
    if _is_html_table_start(stripped) or _looks_like_table_row(stripped):
        return ""
    if stripped.startswith(("```", "~~~")):
        return ""
    return stripped


def _build_image_description(
    alt: str,
    src: str,
    contexts_before: List[str],
    contexts_after: List[str],
    heading_path: str,
) -> str:
    """Build a natural-language description of an image for embedding.

    Enhancement over original:
    - When no alt text is present but heading_path has a step-like last segment
      (e.g. "a. 右键点击网口名称,选择属性"), use that segment as a caption so
      the image is findable via text queries about that operation.
    - Strips image-only chunks that have *no* context at all from becoming
      zero-signal vectors (the caller handles skip logic for truly empty desc).
    """
    location = heading_path or "未命名章节"
    fragments = [f"图片位于【{location}】。"]

    # Derive an implicit caption from the last heading segment when no alt text
    last_heading = heading_path.split(" > ")[-1].strip() if heading_path else ""
    if alt:
        fragments.append(f'\u56fe\u7247\u6807\u9898\u4e3a\u201c{alt}\u201d\u3002')
    elif last_heading and last_heading not in ("", location):
        # Use the most specific heading as an implicit description.
        fragments.append(f"该图片说明了操作步骤：{last_heading}。")

    fragments.append(f"图片路径为 {src}。")
    if contexts_before:
        fragments.append(f"图片前文：{' '.join(contexts_before)}。")
    if contexts_after:
        fragments.append(f"图片后文：{' '.join(contexts_after)}。")
    return " ".join(fragment for fragment in fragments if fragment).strip()


def _build_embedding_text(display_content: str, heading_path: str, block_type: str, metadata: Dict[str, Any]) -> str:
    # Normalise heading path to collapse PDF-extracted Unicode variants before
    # writing into the embedding text so BGE-M3 tokenises correctly.
    heading_path = _nfkc(heading_path)
    tags: List[str] = []
    if heading_path:
        tags.append(f"[Path: {heading_path}]")
    tags.append(f"[Type: {block_type}]")
    title = _nfkc(str(metadata.get("title") or "")).strip()
    if title:
        tags.append(f"[Title: {title}]")

    details: List[str] = []
    semantic_description = str(metadata.get("semantic_description") or "").strip()
    if semantic_description:
        details.append(semantic_description)

    parts = [part for part in (" ".join(tags).strip(), "\n".join(details).strip(), display_content.strip()) if part]
    return "\n\n".join(parts).strip()


def _merge_small_preamble(items: List[ChunkItem], min_section_chars: int) -> List[ChunkItem]:
    final: List[ChunkItem] = []
    preamble_buf: List[ChunkItem] = []

    for item in items:
        if not item.heading_path and len(item.display_content.strip()) < min_section_chars * 2:
            preamble_buf.append(item)
            continue
        if preamble_buf:
            final.append(_merge_items(preamble_buf))
            preamble_buf = []
        final.append(item)

    if preamble_buf:
        final.append(_merge_items(preamble_buf))

    return final


def _merge_items(items: Sequence[ChunkItem]) -> ChunkItem:
    merged_display = "\n\n".join(item.display_content.strip() for item in items if item.display_content.strip())
    metadata = {"path": "", "type": "text", "title": ""}
    return ChunkItem(
        content=merged_display,
        page=items[0].page if items else 0,
        coords=[],
        heading_path="",
        block_type="text",
        metadata=metadata,
        display_content=merged_display,
        embedding_text=_build_embedding_text(merged_display, "", "text", metadata),
    )


def _parse_markdown_heading(text: str) -> Optional[Tuple[int, str]]:
    first_line = text.splitlines()[0].strip()
    match = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", first_line)
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


def _split_markdown_section(body: str, heading_path: List[str], max_chars: int, overlap_chars: int) -> List[str]:
    del heading_path  # kept for compatibility with older callers
    body = body.strip()
    if len(body) <= max_chars:
        return [body] if body else []

    chunks: List[str] = []
    start = 0
    while start < len(body):
        end = min(start + max_chars, len(body))
        if end < len(body):
            boundary = _find_markdown_boundary(body, start, end)
            if boundary > start:
                end = boundary
        part = body[start:end].strip()
        if part:
            chunks.append(part)
        if end >= len(body):
            break
        start = max(start + 1, end - overlap_chars)
    return chunks


def _find_markdown_boundary(text: str, start: int, end: int) -> int:
    lower_bound = start + int((end - start) * 0.6)
    candidates = [
        text.rfind("\n\n", lower_bound, end),
        text.rfind("\n", lower_bound, end),
        text.rfind("。", lower_bound, end),
        text.rfind("；", lower_bound, end),
        text.rfind("，", lower_bound, end),
        text.rfind(".", lower_bound, end),
        text.rfind(";", lower_bound, end),
        text.rfind(",", lower_bound, end),
        text.rfind(" ", lower_bound, end),
    ]
    return max(candidates)


def _extract_marker_page_number(line: str) -> Optional[int]:
    match = _PAGE_MARKER_RE.match(line.strip())
    if match:
        return int(match.group(1))
    if not line.isdigit():
        return None
    page = int(line)
    return page + 1 if page == 0 else page


def _is_markdown_table_start(lines: List[str], index: int) -> bool:
    return index + 1 < len(lines) and _looks_like_table_row(lines[index]) and bool(_TABLE_SEPARATOR_RE.match(lines[index + 1].strip()))


def _looks_like_table_row(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("!["):
        return False
    return "|" in stripped and stripped.count("|") >= 2


def _is_html_table_start(line: str) -> bool:
    return line.lower().startswith("<table")


def _contains_image(line: str) -> bool:
    return bool(_MD_IMAGE_RE.search(line or ""))


def _strip_legacy_heading_prefix(text: str) -> str:
    text = text.strip()
    if text.startswith("Heading path: "):
        parts = text.split("\n\n", 1)
        if len(parts) == 2:
            return parts[1].strip()
    return text


# ---------------------------------------------------------------------------
# Micro-chunk merging
# ---------------------------------------------------------------------------

def _merge_micro_chunks(items: List[ChunkItem], micro_chars: int, max_chars: int) -> List[ChunkItem]:
    """Merge consecutive tiny term/text chunks that share the same heading_path.

    Motivation from UV.chunks.md observations:
    - Chunks 33-36 under "e. 在弹出的新窗口中选择高级" are each ~10 chars:
        "速度和双工 值:1.0Gbps全双工"
        "巨帧数据包 值:9014字节"
        "接收缓冲区 值:2048"
        "传输缓冲区 值:2048"
      These are parameter key-value pairs that belong together as one chunk.
    - Chunks 1-3 are cover metadata (Author/Date/Version) — same issue.

    Rules:
    - Only merges chunks with block_type in {term, text}.
    - Only merges when len(display_content) < micro_chars.
    - Groups by (heading_path, page).
    - Stops accumulating when combined length would exceed max_chars.
    - Does NOT merge across image or table chunks (they act as hard boundaries).
    """
    if not items:
        return items

    result: List[ChunkItem] = []
    buf: List[ChunkItem] = []

    def flush_buf() -> None:
        if not buf:
            return
        if len(buf) == 1:
            result.append(buf[0])
        else:
            result.append(_merge_items(buf))
        buf.clear()

    for item in items:
        is_micro = (
            item.block_type in ("term", "text")
            and len(item.display_content.strip()) < micro_chars
        )
        same_group = (
            buf
            and buf[-1].heading_path == item.heading_path
            and buf[-1].page == item.page
        )
        combined_len = sum(len(b.display_content) for b in buf) + len(item.display_content)

        if is_micro and same_group and combined_len <= max_chars:
            buf.append(item)
        else:
            flush_buf()
            if is_micro:
                buf.append(item)
            else:
                result.append(item)

    flush_buf()
    return result


# ---------------------------------------------------------------------------
# Flat-document windowing
# ---------------------------------------------------------------------------

def _apply_flat_document_windowing(
    items: List[ChunkItem],
    max_chars: int,
    overlap_chars: int,
) -> List[ChunkItem]:
    """For flat documents (no heading hierarchy), group adjacent text/term chunks
    into sliding-window chunks.

    This ensures documents that are pure prose or have only a single top-level
    heading (e.g. a FAQ page, a plain README, a log file) still produce
    meaningful, non-trivial chunks rather than hundreds of micro-fragments.

    Images, tables, parameter_tables, and code blocks are passed through
    unchanged — they are self-contained and should not be merged with text.

    Algorithm:
    1. Walk items. Accumulate text/term items while total chars <= max_chars.
    2. When limit is reached, emit the accumulated window.
    3. Back-off by overlap_chars worth of content for the next window.
    4. Non-text items (image, table, code) act as hard flush boundaries and
       are emitted directly.
    """
    _PASSTHROUGH_TYPES = {"image", "table", "parameter_table", "code"}
    result: List[ChunkItem] = []
    window: List[ChunkItem] = []
    window_chars = 0

    def flush_window() -> None:
        nonlocal window, window_chars
        if not window:
            return
        if len(window) == 1:
            result.append(window[0])
        else:
            result.append(_merge_items(window))
        # Keep overlap: drop items from the front until we're within overlap budget
        total = sum(len(w.display_content) for w in window)
        while window and total - len(window[0].display_content) > overlap_chars:
            total -= len(window[0].display_content)
            window.pop(0)
        window_chars = sum(len(w.display_content) for w in window)

    for item in items:
        if item.block_type in _PASSTHROUGH_TYPES:
            flush_window()
            window.clear()
            window_chars = 0
            result.append(item)
            continue

        item_len = len(item.display_content)
        if window_chars + item_len > max_chars and window:
            flush_window()

        window.append(item)
        window_chars += item_len

    flush_window()
    window.clear()
    return result
