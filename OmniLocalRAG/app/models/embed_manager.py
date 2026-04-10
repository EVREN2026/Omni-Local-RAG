import gc
import faulthandler
import os
import sys
import warnings
from typing import List, Optional

from app.utils import config as cfg
from app.utils.logger import logger


class EmbedManager:
    """Singleton BGE-M3 embedding model — always resident in memory."""

    _instance: Optional["EmbedManager"] = None
    _model = None  # SentenceTransformer | None
    _device = "cpu"
    _last_error = ""

    def __new__(cls) -> "EmbedManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def last_error(self) -> str:
        return self._last_error

    def load(self) -> bool:
        if self._model is not None:
            return True
        self._last_error = ""
        try:
            SentenceTransformer = self._import_sentence_transformer()

            device = self._resolve_device(cfg.get("embed.device", "cpu"))
            try:
                self._model = self._new_model(SentenceTransformer, device)
            except Exception as e:
                if not device.startswith("cuda") or not self._is_cuda_error(e):
                    raise
                logger.warning(f"BGE-M3 CUDA load failed ({e}); loading embedding model on CPU")
                self._model = self._new_model(SentenceTransformer, "cpu")
                device = "cpu"
            self._device = device
            logger.info(f"BGE-M3 loaded on {device}")
            return True
        except Exception as e:
            self._last_error = self._format_load_error(e)
            logger.error(f"EmbedManager load failed: {self._last_error}", exc_info=True)
            return False

    def _import_sentence_transformer(self):
        devnull = None
        try:
            devnull = open(os.devnull, "w")
            faulthandler.enable(file=devnull)
        except Exception:
            devnull = None

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            return SentenceTransformer
        finally:
            try:
                faulthandler.enable(file=sys.stderr)
            except Exception:
                pass
            if devnull is not None:
                devnull.close()

    @staticmethod
    def _format_load_error(error: Exception) -> str:
        detail = str(error)
        if "c10.dll" in detail or "WinError 1114" in detail or "动态链接库" in detail:
            return (
                "PyTorch 原生 DLL 加载失败，BGE-M3 无法启动 "
                f"({detail})。当前 Python 环境里的 torch 安装不兼容或损坏；"
                "请运行 repair_torch_cpu.bat 重装 CPU 版 PyTorch。"
            )
        return f"BGE-M3 加载失败: {detail}"

    def _new_model(self, model_cls, device: str):
        model_path = str(cfg.abs_path(cfg.get("embed.model_path")))
        # Global offline guard: even on older sentence-transformers versions that
        # do not accept local_files_only, keep HF stack in offline mode.
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("HF_DATASETS_OFFLINE", "1")

        # Prefer explicit local-only loading when supported.
        try:
            return model_cls(model_path, device=device, local_files_only=True)
        except TypeError as e:
            msg = str(e)
            if "local_files_only" not in msg or "unexpected keyword argument" not in msg:
                raise
            # Backward compatibility for older sentence-transformers constructor.
            logger.warning(
                "SentenceTransformer does not support local_files_only; "
                "falling back to legacy constructor with offline env guards"
            )
            return model_cls(model_path, device=device)

    def _resolve_device(self, requested_device: str) -> str:
        device = (requested_device or "cpu").strip().lower()
        if not device.startswith("cuda"):
            return device or "cpu"

        try:
            import torch  # type: ignore

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                if not torch.cuda.is_available():
                    logger.warning("CUDA requested for BGE-M3, but torch reports CUDA unavailable; using CPU")
                    return "cpu"

                device_index = self._parse_cuda_index(device)
                capability = torch.cuda.get_device_capability(device_index)
                supported_arches = set(torch.cuda.get_arch_list())

            required_arch = f"sm_{capability[0]}{capability[1]}"
            if supported_arches and required_arch not in supported_arches:
                device_name = torch.cuda.get_device_name(device_index)
                logger.warning(
                    "CUDA requested for BGE-M3, but installed PyTorch does not support "
                    f"{device_name} compute capability {required_arch}; using CPU"
                )
                return "cpu"

            return device
        except Exception as e:
            logger.warning(f"CUDA compatibility check failed for BGE-M3 ({e}); using CPU")
            return "cpu"

    @staticmethod
    def _parse_cuda_index(device: str) -> int:
        if ":" not in device:
            return 0
        try:
            return int(device.split(":", 1)[1])
        except ValueError:
            return 0

    def unload(self) -> None:
        """Only called on full app exit; BGE-M3 is normally resident."""
        self._model = None
        self._device = "cpu"
        gc.collect()
        logger.info("EmbedManager unloaded")

    def encode(self, texts: List[str]) -> List[List[float]]:
        if self._model is None:
            if not self.load():
                raise RuntimeError(self.last_error or "EmbedManager is not loaded")
        try:
            return self._encode_with_current_model(texts)
        except RuntimeError as e:
            if not self._should_retry_on_cpu(e):
                raise
            logger.warning(f"BGE-M3 CUDA encode failed ({e}); reloading embedding model on CPU and retrying")
            self._reload_on_cpu()
            return self._encode_with_current_model(texts)

    def _encode_with_current_model(self, texts: List[str]) -> List[List[float]]:
        vectors = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def _should_retry_on_cpu(self, error: RuntimeError) -> bool:
        return self._device.startswith("cuda") and self._is_cuda_error(error)

    @staticmethod
    def _is_cuda_error(error: BaseException) -> bool:
        return "cuda" in str(error).lower()

    def _reload_on_cpu(self) -> None:
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

        self._model = None
        gc.collect()

        SentenceTransformer = self._import_sentence_transformer()
        self._model = self._new_model(SentenceTransformer, "cpu")
        self._device = "cpu"
        logger.info("BGE-M3 reloaded on cpu")
