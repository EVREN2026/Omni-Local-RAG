"""
json_db_sync.py — Synchronise .chunks.json (L2) ↔ VectorStore + SQLite (L3).

Three operations
----------------
sync_json_to_db(json_path, progress_cb=None)
    Read a .chunks.json, compare with the VectorStore, and apply incremental
    changes:
      • New chunks (chunk_id not in DB) → embed + insert.
      • Changed chunks (is_manual=True or content differs) → re-embed + update.
      • Deleted chunks (absent from JSON but present in DB for the same
        source_file) → remove from VectorStore.
    progress_cb(done, total) is called after each chunk if provided.

export_db_to_json(source_file, output_path)
    Export all VectorStore rows whose pdf_payload["file"] or anchor tag matches
    source_file to a .chunks.json at output_path (inverse direction).

get_sync_status(json_path) -> dict
    Non-destructive: return counts of {new, changed, deleted, unchanged}.
"""

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.models.chroma_store import ChromaStore
from app.models.embed_manager import EmbedManager
from app.utils import config as cfg
from app.utils.logger import logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(json_path: Path) -> Dict[str, Any]:
    return json.loads(json_path.read_text(encoding="utf-8"))


def _fetch_db_chunks(source_file: str) -> Dict[str, Dict[str, Any]]:
    """Return {chunk_id: row} for all rows in the VectorStore that belong to
    source_file (matched via pdf_payload['file'] field or anchor_id prefix)."""
    chroma = ChromaStore()
    try:
        rows = chroma.list_by_source(source_file)
    except AttributeError:
        # ChromaStore may not have list_by_source — fall back to scan
        rows = _scan_all_chunks(source_file)
    return {r["id"]: r for r in rows}


def _scan_all_chunks(source_file: str) -> List[Dict[str, Any]]:
    """Fallback: load every row from the vectors.db and filter by source."""
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
            results.append(dict(row))
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sync_json_to_db(
    json_path: str | Path,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    auto_delete_missing: bool = False,
) -> Dict[str, int]:
    """Sync a .chunks.json file into VectorStore + SQLite.

    Returns a summary dict: {inserted, updated, deleted, skipped, errors}.
    """
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

    for i, chunk in enumerate(chunks):
        chunk_id = chunk.get("chunk_id") or chunk.get("anchor_id", "")
        content = str(chunk.get("content", "")).strip()
        if not content or not chunk_id:
            counts["skipped"] += 1
            continue

        json_ids.add(chunk_id)
        is_manual = bool(chunk.get("is_manual", False))
        existing = db_chunks.get(chunk_id)

        try:
            if existing is None:
                # New chunk — insert
                [vec] = embed.encode([content])
                chroma.add(
                    content=content,
                    embedding=vec,
                    source_type=str(chunk.get("source_type", data.get("source_type", "markdown"))),
                    anchor_id=chunk_id,
                    pdf_payload={
                        "file": source_file,
                        "page": chunk.get("page", 0),
                        "coords": chunk.get("coords", []),
                    },
                    is_manual=is_manual,
                    doc_id=chunk_id,
                )
                counts["inserted"] += 1
                logger.info(f"json_db_sync: inserted chunk {chunk_id}")

            elif is_manual or content != str(existing.get("content", "")):
                # Changed chunk — re-embed and update
                [vec] = embed.encode([content])
                chroma.update(chunk_id, content, vec)
                counts["updated"] += 1
                logger.info(f"json_db_sync: updated chunk {chunk_id} (is_manual={is_manual})")

            else:
                counts["skipped"] += 1

        except Exception as e:
            logger.error(f"json_db_sync: chunk {chunk_id} failed: {e}", exc_info=True)
            counts["errors"] += 1

        if progress_cb:
            progress_cb(i + 1, total)

    # Optional deletion of chunks present in DB but absent from JSON
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


def export_db_to_json(
    source_file: str,
    output_path: str | Path,
) -> Path:
    """Export all VectorStore rows for source_file to a .chunks.json file.

    This is the inverse of sync_json_to_db — useful when the DB is the source
    of truth and you want to generate a fresh L2 JSON for API optimisation.
    """
    from datetime import datetime, timezone

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = _scan_all_chunks(source_file)
    chunks = []
    for row in rows:
        try:
            payload = json.loads(row.get("pdf_payload") or "{}")
        except Exception:
            payload = {}
        chunks.append(
            {
                "chunk_id": row.get("id", ""),
                "anchor_id": row.get("id", ""),
                "source_type": row.get("source_type", "markdown"),
                "page": payload.get("page", 0),
                "coords": payload.get("coords", []),
                "heading_path": "",
                "block_type": "text",
                "content": row.get("content", ""),
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
    logger.info(f"export_db_to_json: {len(chunks)} chunks → {output_path}")
    return output_path


def get_sync_status(json_path: str | Path) -> Dict[str, int]:
    """Non-destructive diff: count new / changed / unchanged chunks.

    Returns dict with keys: new, changed, unchanged, total.
    Does NOT write to the database.
    """
    json_path = Path(json_path)
    data = _load_json(json_path)
    chunks = data.get("chunks", [])
    source_file = data.get("source_file", json_path.stem)
    db_chunks = _fetch_db_chunks(source_file)

    counts = {"new": 0, "changed": 0, "unchanged": 0, "total": len(chunks)}
    for chunk in chunks:
        chunk_id = chunk.get("chunk_id", "")
        content = str(chunk.get("content", "")).strip()
        if not chunk_id:
            continue
        existing = db_chunks.get(chunk_id)
        if existing is None:
            counts["new"] += 1
        elif bool(chunk.get("is_manual")) or content != str(existing.get("content", "")):
            counts["changed"] += 1
        else:
            counts["unchanged"] += 1
    return counts
