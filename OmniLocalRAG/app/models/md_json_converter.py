"""
md_json_converter.py — Bidirectional converter between .chunks.md and .chunks.json.

Roles in the three-layer data architecture:
  L1 (human layer)   : .chunks.md   — readable, manually editable
  L2 (interface layer): .chunks.json — structured, API/program readable

Public API
----------
md_to_json(md_path, base_json_path=None) -> dict
    Parse a .chunks.md file (possibly hand-edited) and return a chunks dict.
    If base_json_path is supplied, machine-generated fields (vector_dim,
    stored, heading_path …) from the original JSON are merged in; fields the
    human edited (content, notes) are preserved.
    Chunks whose content was changed have is_manual set to True.

json_to_md(chunks_dict, output_path)
    Render a chunks dict as a human-readable .chunks.md file that is
    compatible with the MetadataExporter format, then write to output_path.

roundtrip_safe(md_path) -> bool
    Quick smoke-test: parse md_path → render back to a temp string → re-parse
    → verify chunk count and content match.
"""

import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_CHUNK_HEADER_RE = re.compile(r"^##\s+Chunk\s+(\d+)\s*$")
_META_LINE_RE = re.compile(r"^-\s+(\w[\w_]*):\s*(.*)$")
_CODE_FENCE_RE = re.compile(r"^(`{3,4}|~{3,4})")


def _parse_md_chunk_block(lines: List[str]) -> Dict[str, Any]:
    """Parse the metadata + fenced content block of a single chunk section."""
    chunk: Dict[str, Any] = {}
    i = 0
    # Collect meta lines (- key: value) until the opening code fence
    while i < len(lines):
        line = lines[i]
        m = _META_LINE_RE.match(line.strip())
        if m:
            key, value = m.group(1), m.group(2).strip()
            # Attempt type coercion for known numeric / boolean fields
            if key in ("page", "vector_dim"):
                try:
                    chunk[key] = int(value) if value not in ("None", "null", "") else None
                except ValueError:
                    chunk[key] = value
            elif key in ("stored", "is_manual"):
                chunk[key] = value.lower() in ("true", "1", "yes")
            else:
                chunk[key] = value if value else ""
            i += 1
            continue
        if _CODE_FENCE_RE.match(line.strip()):
            break
        i += 1

    # Collect fenced content
    content_lines: List[str] = []
    if i < len(lines) and _CODE_FENCE_RE.match(lines[i].strip()):
        fence_marker = _CODE_FENCE_RE.match(lines[i].strip()).group(1)
        i += 1  # skip opening fence
        while i < len(lines):
            if lines[i].strip() == fence_marker or lines[i].strip().startswith(fence_marker):
                i += 1  # skip closing fence
                break
            content_lines.append(lines[i])
            i += 1

    chunk["content"] = "\n".join(content_lines).strip()
    return chunk


def _split_md_into_chunk_blocks(text: str) -> List[Tuple[int, List[str]]]:
    """Return list of (chunk_number, lines) for each ## Chunk N section."""
    blocks: List[Tuple[int, List[str]]] = []
    current_num: Optional[int] = None
    current_lines: List[str] = []

    for line in text.splitlines():
        m = _CHUNK_HEADER_RE.match(line.strip())
        if m:
            if current_num is not None:
                blocks.append((current_num, current_lines))
            current_num = int(m.group(1))
            current_lines = []
        else:
            if current_num is not None:
                current_lines.append(line)

    if current_num is not None:
        blocks.append((current_num, current_lines))

    return blocks


def _render_chunk_md(index: int, chunk: Dict[str, Any]) -> List[str]:
    """Render a single chunk dict into Markdown lines."""
    content = str(chunk.get("content", ""))
    fence = "````" if "```" in content else "```"
    heading_path = chunk.get("heading_path", "")
    block_type = chunk.get("block_type", "text")

    meta_lines = [
        f"- chunk_id: {chunk.get('chunk_id', '')}",
        f"- anchor_id: {chunk.get('anchor_id', '')}",
        f"- page: {chunk.get('page', 0)}",
        f"- vector_dim: {chunk.get('vector_dim')}",
        f"- stored: {str(chunk.get('stored', False)).lower()}",
        f"- is_manual: {str(chunk.get('is_manual', False)).lower()}",
        f"- block_type: {block_type}",
    ]
    if heading_path:
        meta_lines.append(f"- heading_path: {heading_path}")
    notes = chunk.get("notes", "")
    if notes:
        meta_lines.append(f"- notes: {notes}")

    lines = [
        f"## Chunk {index}",
        "",
        *meta_lines,
        "",
        f"{fence}text",
        content,
        fence,
        "",
    ]
    return lines


def _render_header_md(payload: Dict[str, Any]) -> List[str]:
    source_type = str(payload.get("source_type", "document"))
    label = "PDF" if source_type.lower() == "pdf" else source_type.title()
    return [
        f"# {label} Chunk Export",
        "",
        f"- Source file: {payload.get('source_file', '')}",
        f"- Source type: {source_type}",
        f"- Chunk count: {payload.get('chunk_count', 0)}",
        f"- Exported at: {payload.get('exported_at', '')}",
        "",
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def md_to_json(
    md_path: str | Path,
    base_json_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Parse a .chunks.md file and return a chunks payload dict.

    If base_json_path is given, machine-generated fields (vector_dim, stored,
    heading_path, chunk_id, anchor_id) are merged from the original JSON so
    that only the human-edited fields are taken from the MD file.

    Chunks whose 'content' differs from the base JSON have is_manual=True.
    """
    md_path = Path(md_path)
    text = md_path.read_text(encoding="utf-8")

    # Parse header metadata
    source_file = ""
    source_type = "markdown"
    exported_at = datetime.now(timezone.utc).isoformat()
    for line in text.splitlines():
        m = _META_LINE_RE.match(line.strip())
        if m:
            key, value = m.group(1), m.group(2).strip()
            if key == "Source_file" or key == "source_file" or "Source file" in line:
                source_file = value
            elif key == "Source_type" or key == "source_type" or "Source type" in line:
                source_type = value
            elif key == "Exported_at" or key == "exported_at" or "Exported at" in line:
                exported_at = value

    blocks = _split_md_into_chunk_blocks(text)

    # Load base JSON for merge if available
    base_chunks: Dict[str, Dict[str, Any]] = {}
    base_list: List[Dict[str, Any]] = []
    if base_json_path and Path(base_json_path).exists():
        try:
            base_data = json.loads(Path(base_json_path).read_text(encoding="utf-8"))
            base_list = base_data.get("chunks", [])
            for c in base_list:
                base_chunks[c.get("chunk_id", "")] = c
            if not source_file:
                source_file = base_data.get("source_file", "")
            if not source_type:
                source_type = base_data.get("source_type", "markdown")
        except Exception:
            pass

    merged_chunks: List[Dict[str, Any]] = []

    for idx, (chunk_num, lines) in enumerate(blocks):
        parsed = _parse_md_chunk_block(lines)
        chunk_id = parsed.get("chunk_id", "")

        # Start with base JSON chunk (preserves machine fields)
        if chunk_id and chunk_id in base_chunks:
            base = dict(base_chunks[chunk_id])
        elif idx < len(base_list):
            base = dict(base_list[idx])
        else:
            base = {}

        new_content = parsed.get("content", "")
        old_content = base.get("content", "")

        # Merge: human-editable fields overwrite base
        merged = {**base}
        merged["content"] = new_content
        merged["display_content"] = new_content
        if parsed.get("notes"):
            merged["notes"] = parsed["notes"]
        if parsed.get("heading_path"):
            merged["heading_path"] = parsed["heading_path"]
        if parsed.get("block_type"):
            merged["block_type"] = parsed["block_type"]

        # Mark as manual if content was changed
        if new_content and new_content != old_content:
            merged["is_manual"] = True
            merged["embedding_text"] = new_content

        # Ensure required fields
        merged.setdefault("chunk_id", chunk_id or f"md-chunk-{chunk_num}")
        merged.setdefault("anchor_id", merged["chunk_id"])
        merged.setdefault("source_type", source_type)
        merged.setdefault("page", 0)
        merged.setdefault("stored", False)
        merged.setdefault("is_manual", False)
        merged.setdefault("display_content", merged.get("content", ""))
        merged.setdefault("embedding_text", merged.get("content", ""))

        merged_chunks.append(merged)

    return {
        "source_file": source_file,
        "source_type": source_type,
        "exported_at": exported_at,
        "chunk_count": len(merged_chunks),
        "chunks": merged_chunks,
    }


def json_to_md(
    chunks_dict: Dict[str, Any],
    output_path: str | Path,
) -> Path:
    """Render a chunks payload dict to a human-readable .chunks.md file.

    The output format is compatible with MetadataExporter so existing tooling
    can read it. Returns the written Path.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = _render_header_md(chunks_dict)
    for index, chunk in enumerate(chunks_dict.get("chunks", []), start=1):
        lines.extend(_render_chunk_md(index, chunk))

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def roundtrip_safe(md_path: str | Path, base_json_path: Optional[str | Path] = None) -> bool:
    """Smoke-test: MD → dict → temp MD → dict, verify chunk count + content."""
    try:
        md_path = Path(md_path)
        first = md_to_json(md_path, base_json_path)

        with tempfile.NamedTemporaryFile(suffix=".chunks.md", delete=False, mode="w", encoding="utf-8") as tmp:
            tmp_path = Path(tmp.name)

        json_to_md(first, tmp_path)
        second = md_to_json(tmp_path)
        tmp_path.unlink(missing_ok=True)

        if first["chunk_count"] != second["chunk_count"]:
            return False
        for a, b in zip(first["chunks"], second["chunks"]):
            if a.get("content", "").strip() != b.get("content", "").strip():
                return False
        return True
    except Exception:
        return False
