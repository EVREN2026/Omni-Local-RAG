import gc
import time
from typing import Iterator, Optional

from app.utils import config as cfg
from app.utils.logger import logger


class LLMManager:
    """Singleton wrapper around llama-cpp-python Llama instance."""

    _instance: Optional["LLMManager"] = None
    _llm = None  # llama_cpp.Llama | None

    def __new__(cls) -> "LLMManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def is_loaded(self) -> bool:
        return self._llm is not None

    def load(self) -> bool:
        if self._llm is not None:
            return True
        t0 = time.perf_counter()
        try:
            from llama_cpp import Llama  # type: ignore

            model_path = str(cfg.abs_path(cfg.get("llm.model_path")))
            self._llm = Llama(
                model_path=model_path,
                n_gpu_layers=cfg.get("llm.n_gpu_layers", 0),
                n_ctx=cfg.get("llm.n_ctx", 4096),
                verbose=False,
            )
            elapsed = (time.perf_counter() - t0) * 1000
            logger.info(f"LLM loaded in {elapsed:.0f}ms")
            return True
        except FileNotFoundError:
            logger.error("GGUF model file not found")
            return False
        except Exception as e:
            logger.error(f"LLM load failed: {e}", exc_info=True)
            return False

    def unload(self, reason: str = "manual") -> None:
        if self._llm is None:
            return
        self._llm = None
        gc.collect()
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        logger.info(f"LLM unloaded (reason={reason})")

    def generate(self, prompt: str, stream: bool = True) -> Iterator[str]:
        if self._llm is None:
            raise RuntimeError("LLM is not loaded")
        params = {
            "max_tokens": cfg.get("llm.max_tokens", 512),
            "temperature": cfg.get("llm.temperature", 0.7),
            "repeat_penalty": cfg.get("llm.repeat_penalty", 1.1),
            "stream": stream,
        }
        if stream:
            for chunk in self._llm(prompt, **params):
                token = chunk["choices"][0]["text"]
                if token:
                    yield token
        else:
            result = self._llm(prompt, **params)
            yield result["choices"][0]["text"]
