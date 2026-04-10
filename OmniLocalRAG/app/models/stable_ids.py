"""Stable identifiers for document chunks."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import MutableSet, Optional


def stable_chunk_id(
    source_file: str,
    source_type: str,
    page: object,
    heading_path: object,
    content: object,
    used_ids: Optional[MutableSet[str]] = None,
) -> str:
    """Return a deterministic chunk/anchor id for repeated chunking.

    The id is stable for the same source file, page, heading path, and chunk
    content. If duplicate chunks produce the same base id in one export, a
    deterministic numeric suffix is appended.
    """

    source_name = Path(str(source_file or "document")).name
    label = _safe_label(Path(source_name).stem or "document")
    source_digest = _digest(source_name, length=8)
    content_key = "\n".join(
        [
            str(source_type or "document").strip().lower(),
            source_name,
            str(page if page not in ("", None) else 0),
            _normalize_text(heading_path),
            _normalize_text(content),
        ]
    )
    content_digest = _digest(content_key, length=14)
    page_label = _safe_label(f"p{page if page not in ('', None) else 0}")
    base_id = f"{_safe_label(source_type or 'document')}:{label}-{source_digest}:{page_label}:{content_digest}"

    if used_ids is None:
        return base_id

    candidate = base_id
    index = 2
    while candidate in used_ids:
        candidate = f"{base_id}-{index}"
        index += 1
    used_ids.add(candidate)
    return candidate


def _normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _safe_label(value: object, max_len: int = 36) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._-")
    return (cleaned[:max_len].strip("._-") or "item").lower()


def _digest(value: object, length: int) -> str:
    return hashlib.sha1(str(value or "").encode("utf-8", errors="ignore")).hexdigest()[:length]
