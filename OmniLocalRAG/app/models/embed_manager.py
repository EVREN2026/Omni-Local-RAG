import gc
from typing import List, Optional

from app.utils import config as cfg
from app.utils.logger import logger


class EmbedManager:
    """Singleton BGE-M3 embedding model — always resident in memory."""

    _instance: Optional["EmbedManager"] = None
    _model = None  # SentenceTransformer | None

    def __new__(cls) -> "EmbedManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> bool:
        if self._model is not None:
            return True
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            model_path = str(cfg.abs_path(cfg.get("embed.model_path")))
            device = cfg.get("embed.device", "cpu")
            self._model = SentenceTransformer(model_path, device=device)
            logger.info(f"BGE-M3 loaded on {device}")
            return True
        except Exception as e:
            logger.error(f"EmbedManager load failed: {e}", exc_info=True)
            return False

    def unload(self) -> None:
        """Only called on full app exit; BGE-M3 is normally resident."""
        self._model = None
        gc.collect()
        logger.info("EmbedManager unloaded")

    def encode(self, texts: List[str]) -> List[List[float]]:
        if self._model is None:
            raise RuntimeError("EmbedManager is not loaded")
        vectors = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()
