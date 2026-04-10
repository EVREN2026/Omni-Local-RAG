"""
VectorStore — SQLite + numpy drop-in replacement for ChromaDB.

Why not ChromaDB: chromadb 0.5.x depends on hnswlib whose prebuilt Windows
wheels use AVX / AVX-512 instructions unavailable on many CPUs, causing a
fatal access violation at import time that cannot be caught by Python.

This implementation uses only SQLite (stdlib) and numpy (already required by
sentence-transformers) — no C++ build tools, no DLL issues.

API is identical to the original ChromaStore so no other file needs changing.
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

import numpy as np

from app.utils import config as cfg
from app.utils.logger import logger

_DB_FILE = "data/vectors/vectors.db"

_DDL = """
CREATE TABLE IF NOT EXISTS vectors (
    id            TEXT PRIMARY KEY,
    content       TEXT NOT NULL,
    embedding     BLOB NOT NULL,       -- float32 raw bytes
    source_type   TEXT NOT NULL,
    anchor_id     TEXT DEFAULT '',
    pdf_payload   TEXT DEFAULT '{}',
    video_payload TEXT DEFAULT '{}',
    is_manual     INTEGER DEFAULT 0,
    created_at    TEXT,
    version       INTEGER DEFAULT 1
);
"""


class ChromaStore:
    """SQLite + numpy vector store. Zero external C++ dependencies."""

    _instance: Optional["ChromaStore"] = None
    _db_path: Optional[Path] = None
    COLLECTION_NAME = "omni_rag"   # kept for API compatibility

    def __new__(cls) -> "ChromaStore":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ------------------------------------------------------------------
    # Connection / init
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        if self._db_path is not None:
            return True
        try:
            db_path = cfg.abs_path(_DB_FILE)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_path = db_path
            with self._conn() as conn:
                conn.executescript(_DDL)
            logger.info(f"VectorStore connected ({db_path})")
            return True
        except Exception as e:
            logger.error(f"VectorStore connect failed: {e}", exc_info=True)
            return False

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(
        self,
        content: str,
        embedding: List[float],
        source_type: str,
        anchor_id: Optional[str] = None,
        pdf_payload: Optional[dict] = None,
        video_payload: Optional[dict] = None,
        is_manual: bool = False,
        doc_id: Optional[str] = None,
    ) -> str:
        chunk_id = doc_id or str(uuid.uuid4())
        emb_bytes = np.array(embedding, dtype=np.float32).tobytes()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO vectors
                   (id, content, embedding, source_type, anchor_id,
                    pdf_payload, video_payload, is_manual, created_at, version)
                   VALUES (?,?,?,?,?,?,?,?,?,1)""",
                (
                    chunk_id,
                    content,
                    emb_bytes,
                    source_type,
                    anchor_id or "",
                    json.dumps(pdf_payload or {}),
                    json.dumps(video_payload or {}),
                    int(is_manual),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return chunk_id

    def query(
        self,
        embedding: List[float],
        n_results: int = 5,
    ) -> List[Dict[str, Any]]:
        threshold = cfg.get("retrieval.distance_threshold", 0.7)
        try:
            with self._conn() as conn:
                rows = conn.execute("SELECT * FROM vectors").fetchall()

            if not rows:
                return []

            # Normalise query vector once
            q = np.array(embedding, dtype=np.float32)
            q_norm = np.linalg.norm(q)
            if q_norm > 0:
                q = q / q_norm

            # Build matrix of all stored vectors (N × D)
            mat = np.frombuffer(
                b"".join(r["embedding"] for r in rows), dtype=np.float32
            ).reshape(len(rows), -1)

            # Row-normalise then cosine distance = 1 - dot
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            mat = mat / norms
            dists = (1.0 - mat @ q).tolist()

            # Sort by distance, keep top-n under threshold
            ranked = sorted(zip(dists, range(len(rows))), key=lambda x: x[0])
            results: List[Dict[str, Any]] = []
            for dist, idx in ranked[:n_results]:
                if dist > threshold:
                    break
                row = rows[idx]
                entry: Dict[str, Any] = dict(row)
                entry["distance"] = float(dist)
                entry["document"] = entry["content"]
                try:
                    entry["pdf_payload"]   = json.loads(entry.get("pdf_payload", "{}"))
                    entry["video_payload"] = json.loads(entry.get("video_payload", "{}"))
                except json.JSONDecodeError:
                    pass
                results.append(entry)
            return results

        except Exception as e:
            logger.error(f"VectorStore query failed: {e}", exc_info=True)
            return []

    def update(
        self,
        doc_id: str,
        content: str,
        embedding: List[float],
        extra_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        emb_bytes = np.array(embedding, dtype=np.float32).tobytes()
        with self._conn() as conn:
            conn.execute(
                """UPDATE vectors
                   SET content=?, embedding=?, is_manual=1, version=version+1
                   WHERE id=?""",
                (content, emb_bytes, doc_id),
            )

    def delete(self, doc_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM vectors WHERE id=?", (doc_id,))

    def count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

    def list_by_source(self, source_name: str) -> List[Dict[str, Any]]:
        """Return all vectors whose pdf_payload or video_payload references source_name.

        Implements the interface expected by json_db_sync._fetch_db_chunks().
        Uses a LIKE match on the JSON payload fields — sufficient for exact file names.
        """
        if not self._db_path:
            return []
        try:
            pattern = f'%{source_name}%'
            with self._conn() as conn:
                rows = conn.execute(
                    """SELECT * FROM vectors
                       WHERE pdf_payload LIKE ?
                          OR video_payload LIKE ?
                          OR content LIKE ?""",
                    (pattern, pattern, pattern),
                ).fetchall()
            results = []
            for row in rows:
                entry = dict(row)
                try:
                    entry["pdf_payload"]   = json.loads(entry.get("pdf_payload", "{}"))
                    entry["video_payload"] = json.loads(entry.get("video_payload", "{}"))
                except json.JSONDecodeError:
                    pass
                results.append(entry)
            return results
        except Exception as e:
            logger.error(f"list_by_source({source_name}) failed: {e}", exc_info=True)
            return []
