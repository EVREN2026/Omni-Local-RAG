"""
Synchronise .chunks.json (L2) <-> VectorStore (L3).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.models.chroma_store import ChromaStore
from app.models.embed_manager import EmbedManager
from app.utils import config as cfg
from app.utils.logger import logger


def _load_json(json_path: Path) -> Dict[str, Any]:
    return json.loads(json_path.read_text(encoding="utf-8"))


def _fetch_db_chunks(source_file: str) -> Dict[str, Dict[str, Any]]:
    chroma = ChromaStore()
    try:
        rows = chroma.list_by_source(source_file)
    except AttributeError:
        rows = _scan_all_chunks(source_file)
    return {r["id"]: r for r in rows}


def _scan_all_chunks(source_file: str) -> List[Dict[str, Any]]:
    import sqlite3

    db_path = cfg.abs_path(cfg.get("vectors.db_path", "data/vectors/vectors.db"))
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, content, pdf_payload, source_type, is_manual FROM vectors"
        ).fetchall()
    finally:
        conn.close()

    results = []
    fname = Path(source_file).name
    for row in rows:
        try:
            payload = json.loads(row["pdf_payload"] or "{}")
        except Exception:
            payload = {}
        if payload.get("file") == fname or payload.get("file") == source_file:
            entry = dict(row)
            entry["pdf_payload"] = payload
            results.append(entry)
    return results


def _chunk_payload_from_json(source_file: str, chunk: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "file": source_file,
        "page": chunk.get("page", 0),
        "coords": chunk.get("coords", []),
        "heading_path": chunk.get("heading_path", ""),
        "block_type": chunk.get("block_type", "text"),
        "display_content": chunk.get("display_content", chunk.get("content", "")),
        "embedding_text": chunk.get("embedding_text", chunk.get("content", "")),
        "metadata": chunk.get("metadata", {}),
    }


def _db_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("pdf_payload", {})
    return payload if isinstance(payload, dict) else {}


def _chunk_changed(chunk: Dict[str, Any], existing: Dict[str, Any]) -> bool:
    """Return True when the chunk's content or embedding_text has materially
    changed compared to what is stored in the vector DB.

    Previous behaviour: return True unconditionally when is_manual=True.
    This caused every sync of UV.chunks.json (all 236 chunks have is_manual=True)
    to re-embed every chunk even when nothing changed — wasting ~30-120s of CPU.

    New behaviour:
    - Compare content text first (fast path).
    - Compare embedding_text stored in the DB payload (covers heading_path /
      semantic_description changes produced by the chunker).
    - Only force re-embed when user explicitly marks a chunk with
      ``user_edited: true`` (a new opt-in field distinct from is_manual).
      The legacy ``is_manual`` flag now controls *how* the record is stored
      (INSERT OR REPLACE vs UPDATE) but no longer forces re-embedding.
    """
    # 1. Content text changed
    if str(chunk.get("content", "")).strip() != str(existing.get("content", "")).strip():
        return True

    # 2. embedding_text changed (captures heading_path / semantic_description edits)
    db_payload = _db_payload(existing)
    stored_embedding_text = str(db_payload.get("embedding_text", "")).strip()
    current_embedding_text = str(chunk.get("embedding_text", chunk.get("content", ""))).strip()
    if stored_embedding_text and current_embedding_text != stored_embedding_text:
        return True

    # 3. Explicit user edit flag (opt-in, distinct from is_manual)
    if bool(chunk.get("user_edited", False)):
        return True

    return False


def sync_json_to_db(
    json_path: str | Path,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    auto_delete_missing: bool = False,
) -> Dict[str, int]:
    json_path = Path(json_path)
    data = _load_json(json_path)
    chunks = data.get("chunks", [])
    source_file = data.get("source_file", json_path.stem)
    total = len(chunks)

    embed = EmbedManager()
    if hasattr(embed, "load") and not getattr(embed, "is_loaded", False):
        if not embed.load():
            raise RuntimeError(
                f"Embedding model failed to load: {getattr(embed, 'last_error', '')}"
            )

    chroma = ChromaStore()
    db_chunks = _fetch_db_chunks(source_file)

    counts = {"inserted": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": 0}
    json_ids = set()
    t0 = time.perf_counter()

    for index, chunk in enumerate(chunks):
        chunk_id = chunk.get("chunk_id") or chunk.get("anchor_id", "")
        content = str(chunk.get("content", "")).strip()
        embedding_text = str(chunk.get("embedding_text", content)).strip() or content
        if not content or not chunk_id:
            counts["skipped"] += 1
            continue

        json_ids.add(chunk_id)
        is_manual = bool(chunk.get("is_manual", False))
        existing = db_chunks.get(chunk_id)
        payload = _chunk_payload_from_json(source_file, chunk)

        try:
            if existing is None:
                sparse_payload = None
                embed_backend = getattr(embed, "backend", "")
                if hasattr(embed, "encode_payloads"):
                    [embedding_payload] = embed.encode_payloads([embedding_text], return_sparse=True)
                    vec = list(embedding_payload.get("dense") or [])
                    sparse_payload = embedding_payload.get("sparse") or None
                else:
                    [vec] = embed.encode([embedding_text])
                chroma.add(
                    content=content,
                    embedding=vec,
                    source_type=str(chunk.get("source_type", data.get("source_type", "markdown"))),
                    anchor_id=chunk_id,
                    pdf_payload=payload,
                    sparse_payload=sparse_payload,
                    embed_backend=embed_backend,
                    is_manual=is_manual,
                    doc_id=chunk_id,
                )
                counts["inserted"] += 1
                logger.info(f"json_db_sync: inserted chunk {chunk_id}")
            elif _chunk_changed(chunk, existing):
                sparse_payload = None
                embed_backend = getattr(embed, "backend", "")
                if hasattr(embed, "encode_payloads"):
                    [embedding_payload] = embed.encode_payloads([embedding_text], return_sparse=True)
                    vec = list(embedding_payload.get("dense") or [])
                    sparse_payload = embedding_payload.get("sparse") or None
                else:
                    [vec] = embed.encode([embedding_text])
                chroma.update(
                    chunk_id,
                    content,
                    vec,
                    extra_meta={
                        "source_type": str(chunk.get("source_type", data.get("source_type", "markdown"))),
                        "anchor_id": chunk_id,
                        "pdf_payload": payload,
                        "sparse_payload": sparse_payload,
                        "embed_backend": embed_backend,
                        "is_manual": is_manual,
                    },
                )
                counts["updated"] += 1
                logger.info(f"json_db_sync: updated chunk {chunk_id} (is_manual={is_manual})")
            else:
                counts["skipped"] += 1
        except Exception as e:
            logger.error(f"json_db_sync: chunk {chunk_id} failed: {e}", exc_info=True)
            counts["errors"] += 1

        if progress_cb:
            progress_cb(index + 1, total)

    if auto_delete_missing:
        for db_id in set(db_chunks.keys()) - json_ids:
            try:
                chroma.delete(db_id)
                counts["deleted"] += 1
                logger.info(f"json_db_sync: deleted orphan chunk {db_id}")
            except Exception as e:
                logger.error(f"json_db_sync: delete {db_id} failed: {e}")

    elapsed = time.perf_counter() - t0
    logger.info(
        f"json_db_sync: {source_file} — "
        f"inserted={counts['inserted']} updated={counts['updated']} "
        f"skipped={counts['skipped']} errors={counts['errors']} "
        f"in {elapsed:.1f}s"
    )
    return counts


def export_db_to_json(source_file: str, output_path: str | Path) -> Path:
    from datetime import datetime, timezone

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = _scan_all_chunks(source_file)
    chunks = []
    for row in rows:
        payload = _db_payload(row)
        chunks.append(
            {
                "chunk_id": row.get("id", ""),
                "anchor_id": row.get("id", ""),
                "source_type": row.get("source_type", "markdown"),
                "page": payload.get("page", 0),
                "coords": payload.get("coords", []),
                "heading_path": payload.get("heading_path", ""),
                "block_type": payload.get("block_type", "text"),
                "content": row.get("content", ""),
                "display_content": payload.get("display_content", row.get("content", "")),
                "embedding_text": payload.get("embedding_text", row.get("content", "")),
                "metadata": payload.get("metadata", {}),
                "vector_dim": None,
                "stored": True,
                "is_manual": bool(row.get("is_manual", False)),
            }
        )

    payload_out = {
        "source_file": Path(source_file).name,
        "source_type": chunks[0]["source_type"] if chunks else "markdown",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "chunk_count": len(chunks),
        "chunks": chunks,
    }
    output_path.write_text(
        json.dumps(payload_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"export_db_to_json: {len(chunks)} chunks -> {output_path}")
    return output_path


def get_sync_status(json_path: str | Path) -> Dict[str, int]:
    json_path = Path(json_path)
    data = _load_json(json_path)
    chunks = data.get("chunks", [])
    source_file = data.get("source_file", json_path.stem)
    db_chunks = _fetch_db_chunks(source_file)

    counts = {"new": 0, "changed": 0, "unchanged": 0, "total": len(chunks)}
    for chunk in chunks:
        chunk_id = chunk.get("chunk_id", "")
        if not chunk_id:
            continue
        existing = db_chunks.get(chunk_id)
        if existing is None:
            counts["new"] += 1
        elif _chunk_changed(chunk, existing):
            counts["changed"] += 1
        else:
            counts["unchanged"] += 1
    return counts
