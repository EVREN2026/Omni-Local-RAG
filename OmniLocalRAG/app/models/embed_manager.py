import gc
import faulthandler
import inspect
import os
import sys
import threading
import warnings
from typing import Any, Dict, List, Optional, Tuple

from app.utils import config as cfg
from app.utils.logger import logger


class EmbedManager:
    """Singleton BGE-M3 embedding model kept resident in memory."""

    _instance: Optional["EmbedManager"] = None
    _model = None
    _device = "cpu"
    _backend = "sentence_transformers"
    _last_error = ""
    _loading = False
    _load_cond = threading.Condition(threading.RLock())

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

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def supports_sparse(self) -> bool:
        return self._backend == "flagembedding"

    @property
    def supports_colbert(self) -> bool:
        return self._backend == "flagembedding"

    def load(self) -> bool:
        with self._load_cond:
            if self._model is not None:
                return True
            while self._loading:
                self._load_cond.wait()
                if self._model is not None:
                    return True
                if self._last_error:
                    return False
            self._loading = True
            self._last_error = ""

        try:
            device = self._resolve_device(cfg.get("embed.device", "cpu"))
            try:
                model, backend = self._new_model(device)
            except Exception as exc:
                if not device.startswith("cuda") or not self._is_cuda_error(exc):
                    raise
                logger.warning(f"BGE-M3 CUDA load failed ({exc}); loading embedding model on CPU")
                model, backend = self._new_model("cpu")
                device = "cpu"

            with self._load_cond:
                self._model = model
                self._device = device
                self._backend = backend
                self._last_error = ""
                logger.info(f"BGE-M3 loaded on {device} via {backend}")
                return True
        except Exception as exc:
            formatted = self._format_load_error(exc)
            with self._load_cond:
                self._model = None
                self._device = "cpu"
                self._backend = "sentence_transformers"
                self._last_error = formatted
            logger.error(f"EmbedManager load failed: {formatted}", exc_info=True)
            return False
        finally:
            with self._load_cond:
                self._loading = False
                self._load_cond.notify_all()

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
    def _import_flag_embedding():
        from FlagEmbedding import BGEM3FlagModel  # type: ignore

        return BGEM3FlagModel

    @staticmethod
    def _format_load_error(error: Exception) -> str:
        detail = str(error)
        detail_lower = detail.lower()

        if "no module named 'flagembedding'" in detail_lower:
            return (
                "BGE-M3 native hybrid backend requires the FlagEmbedding package "
                f"({detail}). Install FlagEmbedding or set embed.backend to sentence_transformers."
            )

        if "c10.dll" in detail or "winerror 1114" in detail_lower:
            return (
                "PyTorch native DLL failed to load, so BGE-M3 could not start "
                f"({detail}). The torch installation in the current Python environment "
                "is incompatible or damaged. Run repair_torch_cpu.bat or repair_torch_cuda.bat."
            )

        if "torchvision::nms" in detail_lower or "could not import module 'pretrainedmodel'" in detail_lower:
            return (
                "BGE-M3 failed to load because torch / torchvision / transformers are not a compatible set "
                f"({detail}). This usually means torch and torchvision were installed from different builds. "
                "Run repair_torch_cuda.bat to reinstall matching CUDA wheels."
            )

        return f"BGE-M3 failed to load: {detail}"

    def _new_model(self, device: str) -> Tuple[Any, str]:
        backend_pref = str(cfg.get("embed.backend", "auto") or "auto").strip().lower()
        model_path = str(cfg.abs_path(cfg.get("embed.model_path")))
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("HF_DATASETS_OFFLINE", "1")

        if backend_pref in {"auto", "flagembedding", "flag"}:
            try:
                flag_model_cls = self._import_flag_embedding()
                return self._new_flag_embedding_model(flag_model_cls, model_path, device), "flagembedding"
            except ModuleNotFoundError:
                if backend_pref != "auto":
                    raise
            except Exception as exc:
                if backend_pref != "auto":
                    raise
                logger.warning(f"FlagEmbedding backend unavailable ({exc}); falling back to sentence-transformers")

        sentence_transformer_cls = self._import_sentence_transformer()
        return self._new_sentence_transformer_model(sentence_transformer_cls, model_path, device), "sentence_transformers"

    def _new_flag_embedding_model(self, model_cls, model_path: str, device: str):
        kwargs: Dict[str, Any] = {}
        if self._supports_kwarg(model_cls, "use_fp16"):
            kwargs["use_fp16"] = device.startswith("cuda")
        if self._supports_kwarg(model_cls, "devices"):
            kwargs["devices"] = device
        return model_cls(model_path, **kwargs)

    def _new_sentence_transformer_model(self, model_cls, model_path: str, device: str):
        if self._supports_local_files_only(model_cls):
            try:
                return model_cls(model_path, device=device, local_files_only=True)
            except TypeError as exc:
                if "local_files_only" not in str(exc).lower():
                    raise

        return model_cls(model_path, device=device)

    @staticmethod
    def _supports_local_files_only(model_cls) -> bool:
        return EmbedManager._supports_kwarg(model_cls, "local_files_only")

    @staticmethod
    def _supports_kwarg(model_cls, keyword: str) -> bool:
        try:
            signature = inspect.signature(model_cls.__init__)
        except Exception:
            return False
        if keyword in signature.parameters:
            return True
        return any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )

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
        except Exception as exc:
            logger.warning(f"CUDA compatibility check failed for BGE-M3 ({exc}); using CPU")
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
        with self._load_cond:
            while self._loading:
                self._load_cond.wait()
            self._model = None
            self._device = "cpu"
            self._backend = "sentence_transformers"
            self._last_error = ""
        gc.collect()
        logger.info("EmbedManager unloaded")

    def encode(self, texts: List[str]) -> List[List[float]]:
        payloads = self.encode_payloads(texts, return_sparse=False, return_colbert=False)
        return [list(item.get("dense") or []) for item in payloads]

    def encode_payloads(
        self,
        texts: List[str],
        *,
        return_sparse: bool = False,
        return_colbert: bool = False,
    ) -> List[Dict[str, Any]]:
        if self._model is None:
            if not self.load():
                raise RuntimeError(self.last_error or "EmbedManager is not loaded")
        try:
            return self._encode_payloads_with_current_model(
                texts,
                return_sparse=return_sparse and self.supports_sparse,
                return_colbert=return_colbert and self.supports_colbert,
            )
        except RuntimeError as exc:
            if not self._should_retry_on_cpu(exc):
                raise
            logger.warning(f"BGE-M3 CUDA encode failed ({exc}); reloading embedding model on CPU and retrying")
            self._reload_on_cpu()
            return self._encode_payloads_with_current_model(
                texts,
                return_sparse=return_sparse and self.supports_sparse,
                return_colbert=return_colbert and self.supports_colbert,
            )

    def _encode_payloads_with_current_model(
        self,
        texts: List[str],
        *,
        return_sparse: bool = False,
        return_colbert: bool = False,
    ) -> List[Dict[str, Any]]:
        if self._backend == "flagembedding":
            return self._encode_with_flagembedding(
                texts,
                return_sparse=return_sparse,
                return_colbert=return_colbert,
            )
        return self._encode_with_sentence_transformers(texts)

    def _encode_with_sentence_transformers(self, texts: List[str]) -> List[Dict[str, Any]]:
        vectors = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        dense_vecs = self._normalize_dense_vecs(vectors, len(texts))
        return [
            {
                "dense": dense_vecs[i],
                "sparse": None,
                "colbert": None,
            }
            for i in range(len(texts))
        ]

    def _encode_with_flagembedding(
        self,
        texts: List[str],
        *,
        return_sparse: bool,
        return_colbert: bool,
    ) -> List[Dict[str, Any]]:
        batch_size = int(cfg.get("embed.batch_size", 12))
        max_length = int(cfg.get("embed.max_length", 8192))
        top_k = int(cfg.get("embed.sparse_top_k", 96))
        encoded = self._model.encode(
            texts,
            batch_size=batch_size,
            max_length=max_length,
            return_dense=True,
            return_sparse=return_sparse,
            return_colbert_vecs=return_colbert,
        )
        dense_vecs = self._normalize_dense_vecs(
            encoded.get("dense_vecs") if isinstance(encoded, dict) else encoded,
            len(texts),
        )
        sparse_vecs = self._normalize_sparse_vecs(
            encoded.get("lexical_weights") if isinstance(encoded, dict) else None,
            len(texts),
            top_k=top_k,
        )
        colbert_vecs = self._normalize_colbert_vecs(
            encoded.get("colbert_vecs") if isinstance(encoded, dict) else None,
            len(texts),
        )
        return [
            {
                "dense": dense_vecs[i],
                "sparse": sparse_vecs[i],
                "colbert": colbert_vecs[i],
            }
            for i in range(len(texts))
        ]

    @staticmethod
    def _normalize_dense_vecs(raw: Any, expected_count: int) -> List[List[float]]:
        values = raw.tolist() if hasattr(raw, "tolist") else raw
        if expected_count == 1 and values and values and isinstance(values[0], (int, float)):
            values = [values]
        return [list(map(float, row)) for row in (values or [])]

    @staticmethod
    def _normalize_sparse_vecs(raw: Any, expected_count: int, top_k: int) -> List[Optional[Dict[str, float]]]:
        if not raw:
            return [None] * expected_count
        normalized: List[Optional[Dict[str, float]]] = []
        for item in raw:
            if not item:
                normalized.append(None)
                continue
            pairs = []
            for key, value in dict(item).items():
                try:
                    weight = float(value)
                except Exception:
                    continue
                if weight == 0:
                    continue
                pairs.append((str(key), weight))
            pairs.sort(key=lambda pair: abs(pair[1]), reverse=True)
            normalized.append({key: weight for key, weight in pairs[:top_k]} or None)
        while len(normalized) < expected_count:
            normalized.append(None)
        return normalized

    @staticmethod
    def _normalize_colbert_vecs(raw: Any, expected_count: int) -> List[Optional[List[List[float]]]]:
        if not raw:
            return [None] * expected_count
        normalized: List[Optional[List[List[float]]]] = []
        for item in raw:
            if item is None:
                normalized.append(None)
                continue
            rows = item.tolist() if hasattr(item, "tolist") else item
            normalized.append([list(map(float, row)) for row in (rows or [])])
        while len(normalized) < expected_count:
            normalized.append(None)
        return normalized

    def _should_retry_on_cpu(self, error: RuntimeError) -> bool:
        return self._device.startswith("cuda") and self._is_cuda_error(error)

    @staticmethod
    def _is_cuda_error(error: BaseException) -> bool:
        return "cuda" in str(error).lower()

    def _reload_on_cpu(self) -> None:
        with self._load_cond:
            while self._loading:
                self._load_cond.wait()
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

        self._model = None
        gc.collect()

        model, backend = self._new_model("cpu")
        self._model = model
        self._device = "cpu"
        self._backend = backend
        logger.info(f"BGE-M3 reloaded on cpu via {backend}")
