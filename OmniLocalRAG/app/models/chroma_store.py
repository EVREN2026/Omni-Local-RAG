import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.utils import config as cfg
from app.utils.logger import logger


class ChromaStore:
    """Thin wrapper around ChromaDB persistent client."""

    _instance: Optional["ChromaStore"] = None
    _client = None
    _collection = None
    COLLECTION_NAME = "omni_rag"

    def __new__(cls) -> "ChromaStore":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def connect(self) -> bool:
        if self._client is not None:
            return True
        try:
            import chromadb  # type: ignore
            from chromadb.config import Settings  # type: ignore

            persist_dir = str(cfg.abs_path("data/chroma"))
            self._client = chromadb.PersistentClient(
                path=persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
            # embedding_function=None: we always supply our own vectors (BGE-M3).
            # Without this, chromadb tries to load ONNXMiniLM which requires
            # onnxruntime DLLs — causes WinError 1114 on many Windows setups.
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
                embedding_function=None,
            )
            logger.info("ChromaDB connected")
            return True
        except Exception as e:
            logger.error(f"ChromaDB connect failed: {e}", exc_info=True)
            return False

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
        metadata: Dict[str, Any] = {
            "id": chunk_id,
            "content": content,
            "source_type": source_type,
            "anchor_id": anchor_id or "",
            "pdf_payload": json.dumps(pdf_payload or {}),
            "video_payload": json.dumps(video_payload or {}),
            "is_manual": is_manual,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "version": 1,
        }
        self._collection.add(
            ids=[chunk_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[metadata],
        )
        return chunk_id

    def query(
        self,
        embedding: List[float],
        n_results: int = 5,
    ) -> List[Dict[str, Any]]:
        threshold = cfg.get("retrieval.distance_threshold", 0.7)
        try:
            res = self._collection.query(
                query_embeddings=[embedding],
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error(f"ChromaDB query failed: {e}", exc_info=True)
            return []

        results = []
        for doc, meta, dist in zip(
            res["documents"][0],
            res["metadatas"][0],
            res["distances"][0],
        ):
            if dist <= threshold:
                entry = dict(meta)
                entry["distance"] = dist
                entry["document"] = doc
                try:
                    entry["pdf_payload"] = json.loads(entry.get("pdf_payload", "{}"))
                    entry["video_payload"] = json.loads(entry.get("video_payload", "{}"))
                except json.JSONDecodeError:
                    pass
                results.append(entry)
        return results

    def update(
        self,
        doc_id: str,
        content: str,
        embedding: List[float],
        extra_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        existing = self._collection.get(ids=[doc_id], include=["metadatas"])
        if not existing["ids"]:
            logger.warning(f"ChromaDB update: id {doc_id} not found")
            return
        meta = existing["metadatas"][0]
        meta["content"] = content
        meta["version"] = int(meta.get("version", 1)) + 1
        meta["is_manual"] = True
        if extra_meta:
            meta.update(extra_meta)
        self._collection.update(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[meta],
        )

    def delete(self, doc_id: str) -> None:
        self._collection.delete(ids=[doc_id])

    def count(self) -> int:
        return self._collection.count()
