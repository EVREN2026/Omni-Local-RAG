import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Generator, List, Optional

from app.utils import config as cfg
from app.utils.logger import logger

_DB_PATH = cfg.abs_path("data/omni.db")

DDL = """
CREATE TABLE IF NOT EXISTS video_transcripts (
    id          TEXT PRIMARY KEY,
    video_file  TEXT NOT NULL,
    start_sec   REAL NOT NULL,
    end_sec     REAL NOT NULL,
    text        TEXT NOT NULL,
    confidence  REAL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS video_clips (
    id               TEXT PRIMARY KEY,
    video_file       TEXT NOT NULL,
    start_sec        REAL NOT NULL,
    end_sec          REAL NOT NULL,
    semantic_summary TEXT NOT NULL,
    chroma_id        TEXT,
    is_manual        INTEGER DEFAULT 1,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cross_modal_map (
    id             TEXT PRIMARY KEY,
    video_clip_id  TEXT NOT NULL REFERENCES video_clips(id),
    pdf_anchor_id  TEXT NOT NULL,
    note           TEXT,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SQLiteStore:
    _instance: Optional["SQLiteStore"] = None

    def __new__(cls) -> "SQLiteStore":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def init(self) -> None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(DDL)
        logger.info("SQLiteStore initialized")

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(_DB_PATH))
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
    # video_transcripts
    # ------------------------------------------------------------------

    def insert_transcript(
        self,
        video_file: str,
        start_sec: float,
        end_sec: float,
        text: str,
        confidence: Optional[float] = None,
    ) -> str:
        tid = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO video_transcripts (id,video_file,start_sec,end_sec,text,confidence) VALUES (?,?,?,?,?,?)",
                (tid, video_file, start_sec, end_sec, text, confidence),
            )
        return tid

    def get_transcripts(self, video_file: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM video_transcripts WHERE video_file=? ORDER BY start_sec",
                (video_file,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # video_clips
    # ------------------------------------------------------------------

    def insert_clip(
        self,
        video_file: str,
        start_sec: float,
        end_sec: float,
        semantic_summary: str,
        chroma_id: Optional[str] = None,
    ) -> str:
        cid = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO video_clips (id,video_file,start_sec,end_sec,semantic_summary,chroma_id) VALUES (?,?,?,?,?,?)",
                (cid, video_file, start_sec, end_sec, semantic_summary, chroma_id),
            )
        return cid

    def update_clip(self, clip_id: str, semantic_summary: str, chroma_id: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE video_clips SET semantic_summary=?, chroma_id=?, updated_at=? WHERE id=?",
                (semantic_summary, chroma_id, now, clip_id),
            )

    def get_clip(self, clip_id: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM video_clips WHERE id=?", (clip_id,)).fetchone()
        return dict(row) if row else None

    def get_clips_by_video(self, video_file: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM video_clips WHERE video_file=? ORDER BY start_sec",
                (video_file,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_clips(self, video_file: str = "") -> List[Dict]:
        with self._conn() as conn:
            if video_file:
                rows = conn.execute(
                    "SELECT * FROM video_clips WHERE video_file=? ORDER BY video_file,start_sec",
                    (video_file,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM video_clips ORDER BY video_file,start_sec,created_at"
                ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # cross_modal_map
    # ------------------------------------------------------------------

    def insert_cross_modal(
        self,
        video_clip_id: str,
        pdf_anchor_id: str,
        note: str = "",
    ) -> str:
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM cross_modal_map WHERE video_clip_id=? AND pdf_anchor_id=? LIMIT 1",
                (video_clip_id, pdf_anchor_id),
            ).fetchone()
            if existing:
                return str(existing["id"])
            mid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO cross_modal_map (id,video_clip_id,pdf_anchor_id,note) VALUES (?,?,?,?)",
                (mid, video_clip_id, pdf_anchor_id, note),
            )
        return mid

    def get_cross_modal_by_clip(self, video_clip_id: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM cross_modal_map WHERE video_clip_id=?",
                (video_clip_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_cross_modal(self) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT
                       m.id,
                       m.video_clip_id,
                       m.pdf_anchor_id,
                       m.note,
                       m.created_at,
                       c.video_file,
                       c.start_sec,
                       c.end_sec,
                       c.semantic_summary
                   FROM cross_modal_map m
                   LEFT JOIN video_clips c ON c.id = m.video_clip_id
                   ORDER BY m.created_at DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def update_cross_modal_anchor(self, map_id: str, pdf_anchor_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE cross_modal_map SET pdf_anchor_id=? WHERE id=?",
                (pdf_anchor_id, map_id),
            )

    def delete_cross_modal(self, map_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM cross_modal_map WHERE id=?", (map_id,))

    # ------------------------------------------------------------------
    # app_config
    # ------------------------------------------------------------------

    def set_config(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO app_config (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_config(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM app_config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
