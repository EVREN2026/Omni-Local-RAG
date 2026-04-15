"""
ChromaStore — SQLite document store for OmniLocalRAG.

Replaces the previous vector-store implementation.  All embedding / sparse
retrieval logic has been removed; search is now driven by the GemmaRouter
which uses LLM-directed SQL queries on heading_path and category.

API keeps the singleton pattern and basic CRUD, but drops all vector
parameters (embedding, sparse_payload, embed_backend).
"""

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from app.utils import config as cfg
from app.utils.logger import logger

_DB_FILE = "data/vectors/vectors.db"
_KEYWORD_RE = re.compile(r"[A-Za-z0-9_.:/\\\\-]{2,}|[\u4e00-\u9fff]{2,}")

_DDL = """
CREATE TABLE IF NOT EXISTS vectors (
    id            TEXT PRIMARY KEY,
    content       TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    anchor_id     TEXT DEFAULT '',
    pdf_payload   TEXT DEFAULT '{}',
    video_payload TEXT DEFAULT '{}',
    is_manual     INTEGER DEFAULT 0,
    category      TEXT DEFAULT 'general',
    heading_path  TEXT DEFAULT '',
    created_at    TEXT,
    version       INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_vectors_category ON vectors(category);
CREATE INDEX IF NOT EXISTS idx_vectors_heading ON vectors(heading_path);
CREATE INDEX IF NOT EXISTS idx_vectors_source_type ON vectors(source_type);
"""


class ChromaStore:
    """SQLite document store.  No external C++ dependencies."""

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

    def _ensure_connected(self) -> bool:
        """Auto-connect if not already connected. Returns True if connected."""
        if self._db_path is not None:
            return True
        return self.connect()

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
            logger.info(f"ChromaStore connected ({db_path})")
            return True
        except Exception as e:
            logger.error(f"ChromaStore connect failed: {e}", exc_info=True)
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
    # Schema migration
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        """Migrate old schema: if legacy vector columns exist, rebuild table."""
        existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(vectors)").fetchall()
        }

        # If old embedding columns exist, we need a full table rebuild
        if "embedding" in existing or "sparse_payload" in existing or "embed_backend" in existing:
            logger.info("Migrating ChromaStore: removing vector columns, adding heading_path ...")
            # Determine which columns exist in the old table so we can
            # safely SELECT them.  Older schemas may lack category/heading_path.
            old_has_category = "category" in existing
            old_has_heading_path = "heading_path" in existing
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS vectors_new (
                    id            TEXT PRIMARY KEY,
                    content       TEXT NOT NULL,
                    source_type   TEXT NOT NULL,
                    anchor_id     TEXT DEFAULT '',
                    pdf_payload   TEXT DEFAULT '{}',
                    video_payload TEXT DEFAULT '{}',
                    is_manual     INTEGER DEFAULT 0,
                    category      TEXT DEFAULT 'general',
                    heading_path  TEXT DEFAULT '',
                    created_at    TEXT,
                    version       INTEGER DEFAULT 1
                );
            """)
            # Build INSERT dynamically based on which columns exist
            old_cols = ["id", "content", "source_type", "anchor_id", "pdf_payload", "video_payload", "is_manual"]
            select_cols = list(old_cols)
            if old_has_category:
                old_cols.append("category")
                select_cols.append("category")
            else:
                select_cols.append("'general' AS category")
            if old_has_heading_path:
                old_cols.append("heading_path")
                select_cols.append("heading_path")
            else:
                select_cols.append("'' AS heading_path")
            old_cols.extend(["created_at", "version"])
            select_cols.extend(["created_at", "version"])

            insert_sql = (
                f"INSERT OR IGNORE INTO vectors_new "
                f"(id, content, source_type, anchor_id, pdf_payload, video_payload, "
                f"is_manual, category, heading_path, created_at, version) "
                f"SELECT {', '.join(select_cols)} FROM vectors"
            )
            conn.execute(insert_sql)
            conn.execute("DROP TABLE vectors")
            conn.execute("ALTER TABLE vectors_new RENAME TO vectors")
            conn.executescript("""
                CREATE INDEX IF NOT EXISTS idx_vectors_category ON vectors(category);
                CREATE INDEX IF NOT EXISTS idx_vectors_heading ON vectors(heading_path);
                CREATE INDEX IF NOT EXISTS idx_vectors_source_type ON vectors(source_type);
            """)
            logger.info("ChromaStore migration complete.")
            return

        # Lightweight: add missing columns on fresh installs
        required = {
            "category": "TEXT DEFAULT 'general'",
            "heading_path": "TEXT DEFAULT ''",
        }
        for column, ddl in required.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE vectors ADD COLUMN {column} {ddl}")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(
        self,
        content: str,
        source_type: str,
        anchor_id: Optional[str] = None,
        pdf_payload: Optional[dict] = None,
        video_payload: Optional[dict] = None,
        is_manual: bool = False,
        doc_id: Optional[str] = None,
        category: str = "general",
        heading_path: str = "",
    ) -> str:
        self._ensure_connected()
        chunk_id = doc_id or str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO vectors
                   (id, content, source_type, anchor_id, pdf_payload, video_payload,
                    is_manual, category, heading_path, created_at, version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
                (
                    chunk_id,
                    content,
                    source_type,
                    anchor_id or "",
                    json.dumps(pdf_payload or {}),
                    json.dumps(video_payload or {}),
                    int(is_manual),
                    category or "general",
                    heading_path or "",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return chunk_id

    def update(
        self,
        doc_id: str,
        content: str,
        extra_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._ensure_connected()
        meta = dict(extra_meta or {})
        pdf_payload = json.dumps(meta.get("pdf_payload", {}))
        source_type = meta.get("source_type")
        anchor_id = meta.get("anchor_id")
        is_manual = int(bool(meta.get("is_manual", True)))
        category = str(meta.get("category", "") or "").strip()
        heading_path = str(meta.get("heading_path", "") or "").strip()
        with self._conn() as conn:
            if (
                source_type is None
                and anchor_id is None
                and "pdf_payload" not in meta
                and not category
                and not heading_path
            ):
                conn.execute(
                    """UPDATE vectors
                       SET content=?, is_manual=1, version=version+1
                       WHERE id=?""",
                    (content, doc_id),
                )
            else:
                existing = conn.execute(
                    "SELECT source_type, anchor_id, pdf_payload, category, heading_path FROM vectors WHERE id=?",
                    (doc_id,),
                ).fetchone()
                if existing is None:
                    return
                conn.execute(
                    """UPDATE vectors
                       SET content=?, source_type=?, anchor_id=?, pdf_payload=?,
                           is_manual=?, category=?, heading_path=?, version=version+1
                       WHERE id=?""",
                    (
                        content,
                        source_type or existing["source_type"],
                        anchor_id or existing["anchor_id"],
                        pdf_payload if "pdf_payload" in meta else existing["pdf_payload"],
                        is_manual,
                        category or existing["category"],
                        heading_path or existing["heading_path"],
                        doc_id,
                    ),
                )

    def delete(self, doc_id: str) -> None:
        self._ensure_connected()
        with self._conn() as conn:
            conn.execute("DELETE FROM vectors WHERE id=?", (doc_id,))

    def count(self) -> int:
        if not self._ensure_connected():
            return 0
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

    # ------------------------------------------------------------------
    # SQL query methods (replacing vector query)
    # ------------------------------------------------------------------

    def list_by_category(self, category: str) -> List[Dict[str, Any]]:
        """Return all chunks in the given category."""
        if not self._ensure_connected():
            return []
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM vectors WHERE category = ?",
                    (category,),
                ).fetchall()
            return [self._row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"list_by_category({category}) failed: {e}", exc_info=True)
            return []

    def list_headings(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return distinct heading_path entries, optionally filtered by category.

        Each entry has: heading_path, category, chunk_count.
        """
        if not self._ensure_connected():
            return []
        try:
            with self._conn() as conn:
                if category:
                    rows = conn.execute(
                        """SELECT heading_path, category, COUNT(*) as chunk_count
                           FROM vectors
                           WHERE category = ? AND heading_path != ''
                           GROUP BY heading_path
                           ORDER BY heading_path""",
                        (category,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT heading_path, category, COUNT(*) as chunk_count
                           FROM vectors
                           WHERE heading_path != ''
                           GROUP BY heading_path
                           ORDER BY heading_path""",
                    ).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"list_headings({category}) failed: {e}", exc_info=True)
            return []

    def get_by_heading(
        self,
        heading_path: str,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return chunks matching a heading_path (prefix match).

        If category is given, further filter by it.
        """
        if not self._ensure_connected():
            return []
        try:
            with self._conn() as conn:
                if category:
                    rows = conn.execute(
                        """SELECT * FROM vectors
                           WHERE heading_path LIKE ? AND category = ?
                           ORDER BY heading_path""",
                        (f"{heading_path}%", category),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT * FROM vectors
                           WHERE heading_path LIKE ?
                           ORDER BY heading_path""",
                        (f"{heading_path}%",),
                    ).fetchall()
            return [self._row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"get_by_heading({heading_path}) failed: {e}", exc_info=True)
            return []

    def search_text(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Simple LIKE text search across content and heading_path.

        Used as a fallback / supplement when LLM routing is unavailable.
        """
        if not self._ensure_connected():
            return []
        try:
            terms = self._extract_query_terms(query)
            if not terms:
                return []
            # Build WHERE clause: each term must match content OR heading_path
            conditions = []
            params: list = []
            for term in terms:
                conditions.append("(content LIKE ? OR heading_path LIKE ?)")
                params.extend([f"%{term}%", f"%{term}%"])

            where = " AND ".join(conditions)
            if category:
                where = f"({where}) AND category = ?"
                params.append(category)

            params.append(limit)
            with self._conn() as conn:
                rows = conn.execute(
                    f"""SELECT * FROM vectors
                        WHERE {where}
                        ORDER BY heading_path
                        LIMIT ?""",
                    params,
                ).fetchall()
            return [self._row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"search_text({query}) failed: {e}", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Existing utility methods
    # ------------------------------------------------------------------

    def list_by_source(self, source_name: str) -> List[Dict[str, Any]]:
        """Return all vectors whose pdf_payload or video_payload references source_name."""
        if not self._ensure_connected():
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
            return [self._row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"list_by_source({source_name}) failed: {e}", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        entry: Dict[str, Any] = dict(row)
        try:
            entry["pdf_payload"] = json.loads(entry.get("pdf_payload", "{}"))
        except json.JSONDecodeError:
            entry["pdf_payload"] = {}
        try:
            entry["video_payload"] = json.loads(entry.get("video_payload", "{}"))
        except json.JSONDecodeError:
            entry["video_payload"] = {}
        entry["document"] = entry["content"]
        return entry

    @staticmethod
    def _extract_query_terms(query_text: str) -> List[str]:
        seen = set()
        terms: List[str] = []
        phrase = " ".join(str(query_text or "").lower().split())
        if phrase:
            seen.add(phrase)
            terms.append(phrase)
        for match in _KEYWORD_RE.finditer(str(query_text or "")):
            token = " ".join(match.group(0).lower().split())
            if len(token) < 2 or token in seen:
                continue
            seen.add(token)
            terms.append(token)
        return terms
