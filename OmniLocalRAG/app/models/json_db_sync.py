"""
Synchronise .chunks.json (L2) <-> ChromaStore (L3).

Embedding logic has been removed — the store now uses heading_path and
category for LLM-directed retrieval instead of vector similarity.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.models.chroma_store import ChromaStore
from app.utils import config as cfg
from app.utils.category_keywords import heuristic_category
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
            "SELECT id, content, pdf_payload, source_type, is_manual, category, heading_path FROM vectors"
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
    """Return True when the chunk's content has materially changed."""
    # 1. Content text changed
    if str(chunk.get("content", "")).strip() != str(existing.get("content", "")).strip():
        return True

    # 2. heading_path changed
    existing_heading = str(existing.get("heading_path", "") or "").strip()
    chunk_heading = str(chunk.get("heading_path", "") or "").strip()
    if chunk_heading and chunk_heading != existing_heading:
        return True

    # 3. Explicit user edit flag
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

    chroma = ChromaStore()
    chroma.connect()
    db_chunks = _fetch_db_chunks(source_file)

    counts = {"inserted": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": 0}
    json_ids = set()
    t0 = time.perf_counter()

    for index, chunk in enumerate(chunks):
        chunk_id = chunk.get("chunk_id") or chunk.get("anchor_id", "")
        content = str(chunk.get("content", "")).strip()
        if not content or not chunk_id:
            counts["skipped"] += 1
            continue

        json_ids.add(chunk_id)
        is_manual = bool(chunk.get("is_manual", False))
        existing = db_chunks.get(chunk_id)
        payload = _chunk_payload_from_json(source_file, chunk)

        # Infer category from source file + heading + content
        heading_path = str(chunk.get("heading_path", "") or "").strip()
        category_blob = f"{source_file} {heading_path} {content[:300]}"
        chunk_category = heuristic_category(category_blob)

        try:
            if existing is None:
                chroma.add(
                    content=content,
                    source_type=str(chunk.get("source_type", data.get("source_type", "markdown"))),
                    anchor_id=chunk_id,
                    pdf_payload=payload,
                    is_manual=is_manual,
                    doc_id=chunk_id,
                    category=chunk_category,
                    heading_path=heading_path,
                )
                counts["inserted"] += 1
                logger.info(f"json_db_sync: inserted chunk {chunk_id}")
            elif _chunk_changed(chunk, existing):
                chroma.update(
                    chunk_id,
                    content,
                    extra_meta={
                        "source_type": str(chunk.get("source_type", data.get("source_type", "markdown"))),
                        "anchor_id": chunk_id,
                        "pdf_payload": payload,
                        "is_manual": is_manual,
                        "category": chunk_category,
                        "heading_path": heading_path,
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

    # Update knowledge graph incrementally
    if counts["inserted"] > 0 or counts["updated"] > 0:
        try:
            from app.models.graph_updater import GraphUpdater
            updater = GraphUpdater()
            # Build chunk list for graph update
            graph_chunks = []
            for chunk in chunks:
                chunk_id = chunk.get("chunk_id") or chunk.get("anchor_id", "")
                if chunk_id:
                    graph_chunks.append({
                        "id": chunk_id,
                        "chunk_id": chunk_id,
                        "content": chunk.get("content", ""),
                        "heading_path": chunk.get("heading_path", ""),
                        "category": heuristic_category(
                            f"{source_file} {chunk.get('heading_path', '')} {chunk.get('content', '')[:300]}"
                        ),
                    })
            if graph_chunks:
                updater.on_chunks_added(graph_chunks, source_file=source_file)
                updater.maybe_recluster()
        except Exception as e:
            logger.debug(f"Graph update after sync skipped: {e}")

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
                "heading_path": row.get("heading_path", "") or payload.get("heading_path", ""),
                "block_type": payload.get("block_type", "text"),
                "content": row.get("content", ""),
                "display_content": payload.get("display_content", row.get("content", "")),
                "embedding_text": payload.get("embedding_text", row.get("content", "")),
                "metadata": payload.get("metadata", {}),
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
