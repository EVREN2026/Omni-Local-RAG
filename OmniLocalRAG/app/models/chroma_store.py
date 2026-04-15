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
import re
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
_KEYWORD_RE = re.compile(r"[A-Za-z0-9_.:/\\\\-]{2,}|[\u4e00-\u9fff]{2,}")

_DDL = """
CREATE TABLE IF NOT EXISTS vectors (
    id            TEXT PRIMARY KEY,
    content       TEXT NOT NULL,
    embedding     BLOB NOT NULL,       -- float32 raw bytes
    sparse_payload TEXT DEFAULT '{}',
    source_type   TEXT NOT NULL,
    anchor_id     TEXT DEFAULT '',
    pdf_payload   TEXT DEFAULT '{}',
    video_payload TEXT DEFAULT '{}',
    is_manual     INTEGER DEFAULT 0,
    created_at    TEXT,
    embed_backend TEXT DEFAULT '',
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
                self._ensure_schema(conn)
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

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(vectors)").fetchall()
        }
        required = {
            "sparse_payload": "TEXT DEFAULT '{}'",
            "embed_backend": "TEXT DEFAULT ''",
        }
        for column, ddl in required.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE vectors ADD COLUMN {column} {ddl}")

    def add(
        self,
        content: str,
        embedding: List[float],
        source_type: str,
        anchor_id: Optional[str] = None,
        pdf_payload: Optional[dict] = None,
        video_payload: Optional[dict] = None,
        sparse_payload: Optional[dict] = None,
        embed_backend: str = "",
        is_manual: bool = False,
        doc_id: Optional[str] = None,
    ) -> str:
        chunk_id = doc_id or str(uuid.uuid4())
        emb_bytes = np.array(embedding, dtype=np.float32).tobytes()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO vectors
                   (id, content, embedding, sparse_payload, source_type, anchor_id,
                    pdf_payload, video_payload, is_manual, created_at, embed_backend, version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
                (
                    chunk_id,
                    content,
                    emb_bytes,
                    json.dumps(sparse_payload or {}),
                    source_type,
                    anchor_id or "",
                    json.dumps(pdf_payload or {}),
                    json.dumps(video_payload or {}),
                    int(is_manual),
                    datetime.now(timezone.utc).isoformat(),
                    str(embed_backend or ""),
                ),
            )
        return chunk_id

    def query(
        self,
        embedding: List[float],
        n_results: int = 5,
        query_text: str = "",
        query_sparse: Optional[Dict[str, float]] = None,
    ) -> List[Dict[str, Any]]:
        threshold = cfg.get("retrieval.distance_threshold", 0.7)
        retrieval_mode = str(cfg.get("retrieval.mode", "hybrid") or "hybrid").strip().lower()
        dense_weight = float(cfg.get("retrieval.hybrid_dense_weight", 0.65))
        sparse_weight = float(cfg.get("retrieval.hybrid_sparse_weight", 0.35))
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

            dense_scores = [max(0.0, 1.0 - float(dist)) for dist in dists]
            sparse_scores = [0.0] * len(rows)
            use_sparse = retrieval_mode != "dense" and bool(query_sparse or str(query_text or "").strip())
            if use_sparse:
                sparse_scores = [self._sparse_score(query_text, row, query_sparse=query_sparse) for row in rows]

            max_dense = max(dense_scores) if dense_scores else 0.0
            max_sparse = max(sparse_scores) if sparse_scores else 0.0
            ranked = []
            for idx, dist in enumerate(dists):
                dense_norm = (dense_scores[idx] / max_dense) if max_dense > 0 else 0.0
                sparse_norm = (sparse_scores[idx] / max_sparse) if max_sparse > 0 else 0.0
                if use_sparse and max_sparse > 0:
                    final_score = dense_weight * dense_norm + sparse_weight * sparse_norm
                else:
                    final_score = dense_norm
                ranked.append((final_score, float(dist), idx, dense_scores[idx], sparse_scores[idx]))
            ranked.sort(key=lambda item: (-item[0], item[1]))

            results: List[Dict[str, Any]] = []
            for final_score, dist, idx, dense_score, sparse_score in ranked:
                if len(results) >= n_results:
                    break
                if dist > threshold and sparse_score <= 0:
                    continue
                row = rows[idx]
                entry: Dict[str, Any] = dict(row)
                entry["distance"] = float(dist)
                entry["dense_score"] = float(dense_score)
                entry["sparse_score"] = float(sparse_score)
                entry["retrieval_score"] = float(final_score)
                entry["retrieval_mode"] = "hybrid" if use_sparse and max_sparse > 0 else "dense"
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

    def _sparse_score(
        self,
        query_text: str,
        row: sqlite3.Row,
        query_sparse: Optional[Dict[str, float]] = None,
    ) -> float:
        doc_text = self._document_sparse_text(row)

        score = 0.0
        if query_sparse:
            score += self._sparse_overlap_score(query_sparse, row)

        if doc_text:
            query_phrase = self._normalize_keyword_text(query_text)
            if query_phrase and query_phrase in doc_text:
                score += 6.0

            for term in self._extract_query_terms(query_text):
                if term and term in doc_text:
                    score += self._term_weight(term)
                    if self._is_precision_term(term):
                        score += 1.5
        return score

    def _sparse_overlap_score(self, query_sparse: Dict[str, float], row: sqlite3.Row) -> float:
        doc_sparse = self._load_sparse_payload(row)
        if not doc_sparse:
            return 0.0
        score = 0.0
        for key, query_weight in query_sparse.items():
            doc_weight = doc_sparse.get(str(key))
            if doc_weight is None:
                continue
            score += float(query_weight) * float(doc_weight)
        return score

    @staticmethod
    def _load_sparse_payload(row: sqlite3.Row) -> Dict[str, float]:
        try:
            payload = json.loads(row["sparse_payload"] or "{}")
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        normalized: Dict[str, float] = {}
        for key, value in payload.items():
            try:
                normalized[str(key)] = float(value)
            except Exception:
                continue
        return normalized

    def _document_sparse_text(self, row: sqlite3.Row) -> str:
        parts: List[str] = [str(row["content"] or "")]
        try:
            payload = json.loads(row["pdf_payload"] or "{}")
        except Exception:
            payload = {}

        if isinstance(payload, dict):
            parts.extend(
                [
                    str(payload.get("heading_path") or ""),
                    str(payload.get("block_type") or ""),
                    str(payload.get("display_content") or ""),
                    str(payload.get("embedding_text") or ""),
                ]
            )
            metadata = payload.get("metadata") or {}
            if isinstance(metadata, dict):
                for key in ("semantic_description", "caption", "alt", "title", "image_path"):
                    parts.append(str(metadata.get(key) or ""))
                search_terms = metadata.get("search_terms")
                if isinstance(search_terms, list):
                    parts.extend(str(term) for term in search_terms)

        return self._normalize_keyword_text(" ".join(part for part in parts if part))

    @staticmethod
    def _normalize_keyword_text(text: str) -> str:
        return " ".join(str(text or "").lower().split())

    @staticmethod
    def _extract_query_terms(query_text: str) -> List[str]:
        seen = set()
        terms: List[str] = []
        phrase = ChromaStore._normalize_keyword_text(query_text)
        if phrase:
            seen.add(phrase)
            terms.append(phrase)
        for match in _KEYWORD_RE.finditer(str(query_text or "")):
            token = ChromaStore._normalize_keyword_text(match.group(0))
            if len(token) < 2 or token in seen:
                continue
            seen.add(token)
            terms.append(token)
        return terms

    @staticmethod
    def _is_precision_term(term: str) -> bool:
        return any(ch.isdigit() for ch in term) or any(ch in "._-:/\\" for ch in term)

    @classmethod
    def _term_weight(cls, term: str) -> float:
        weight = 1.0
        if cls._is_precision_term(term):
            weight += 1.5
        if len(term) >= 6:
            weight += 0.3
        return weight

    def update(
        self,
        doc_id: str,
        content: str,
        embedding: List[float],
        extra_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        emb_bytes = np.array(embedding, dtype=np.float32).tobytes()
        meta = dict(extra_meta or {})
        pdf_payload = json.dumps(meta.get("pdf_payload", {}))
        sparse_payload = meta.get("sparse_payload")
        source_type = meta.get("source_type")
        anchor_id = meta.get("anchor_id")
        embed_backend = str(meta.get("embed_backend", "") or "")
        is_manual = int(bool(meta.get("is_manual", True)))
        with self._conn() as conn:
            if (
                source_type is None
                and anchor_id is None
                and "pdf_payload" not in meta
                and sparse_payload is None
                and not embed_backend
            ):
                conn.execute(
                    """UPDATE vectors
                       SET content=?, embedding=?, is_manual=1, version=version+1
                       WHERE id=?""",
                    (content, emb_bytes, doc_id),
                )
            else:
                existing = conn.execute(
                    "SELECT source_type, anchor_id, pdf_payload, sparse_payload, embed_backend FROM vectors WHERE id=?",
                    (doc_id,),
                ).fetchone()
                if existing is None:
                    return
                conn.execute(
                    """UPDATE vectors
                       SET content=?, embedding=?, source_type=?, anchor_id=?, pdf_payload=?,
                           sparse_payload=?, embed_backend=?, is_manual=?, version=version+1
                       WHERE id=?""",
                    (
                        content,
                        emb_bytes,
                        source_type or existing["source_type"],
                        anchor_id or existing["anchor_id"],
                        pdf_payload if "pdf_payload" in meta else existing["pdf_payload"],
                        json.dumps(sparse_payload or {}) if sparse_payload is not None else existing["sparse_payload"],
                        embed_backend or existing["embed_backend"],
                        is_manual,
                        doc_id,
                    ),
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
