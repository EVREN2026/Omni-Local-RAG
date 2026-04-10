import gc
import json
import os
import subprocess
import sys
import time
from typing import Iterator, Optional

from app.utils import config as cfg
from app.utils.logger import logger


class LLMSubprocessError(RuntimeError):
    pass


class LLMManager:
    """Singleton wrapper around llama-cpp-python Llama instance."""

    _instance: Optional["LLMManager"] = None
    _llm = None  # llama_cpp.Llama | None
    _last_error = ""
    _load_failed = False
    _subprocess_mode = False

    def __new__(cls) -> "LLMManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def is_loaded(self) -> bool:
        return self._llm is not None

    @property
    def last_error(self) -> str:
        return self._last_error

    def load(self) -> bool:
        if cfg.get("llm.subprocess", True):
            model_path = cfg.abs_path(cfg.get("llm.model_path"))
            if not model_path.exists():
                self._last_error = f"GGUF 模型文件不存在: {model_path}"
                self._load_failed = True
                logger.error(self._last_error)
                return False
            self._last_error = ""
            self._load_failed = False
            self._subprocess_mode = True
            logger.info("LLM subprocess mode enabled")
            return True

        if self._llm is not None:
            return True
        if self._load_failed:
            return False
        self._last_error = ""
        t0 = time.perf_counter()
        try:
            n_gpu_layers = int(cfg.get("llm.n_gpu_layers", 0))
            if n_gpu_layers <= 0:
                os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

            from llama_cpp import Llama  # type: ignore

            model_path = cfg.abs_path(cfg.get("llm.model_path"))
            if not model_path.exists():
                self._last_error = f"GGUF 模型文件不存在: {model_path}"
                logger.error(self._last_error)
                return False

            try:
                self._llm = self._create_llama(Llama, str(model_path), n_gpu_layers)
            except Exception as e:
                if n_gpu_layers <= 0:
                    raise
                logger.warning(
                    f"LLM GPU load failed with n_gpu_layers={n_gpu_layers}: {e}; retrying on CPU"
                )
                self._llm = None
                gc.collect()
                self._llm = self._create_llama(Llama, str(model_path), 0)

            elapsed = (time.perf_counter() - t0) * 1000
            logger.info(f"LLM loaded in {elapsed:.0f}ms")
            self._load_failed = False
            return True
        except ImportError as e:
            self._last_error = f"llama-cpp-python 未安装或无法导入: {e}"
            self._load_failed = True
            logger.error(self._last_error, exc_info=True)
            return False
        except FileNotFoundError as e:
            self._last_error = f"GGUF 模型文件不存在: {e}"
            self._load_failed = True
            logger.error(self._last_error, exc_info=True)
            return False
        except OSError as e:
            self._last_error = self._native_backend_error(e)
            self._load_failed = True
            logger.error(f"LLM load failed: {self._last_error}", exc_info=True)
            return False
        except Exception as e:
            self._last_error = f"LLM 加载失败: {e}"
            self._load_failed = True
            logger.error(self._last_error, exc_info=True)
            return False

    def _create_llama(self, llama_cls, model_path: str, n_gpu_layers: int):
        return llama_cls(
            model_path=model_path,
            n_gpu_layers=n_gpu_layers,
            n_ctx=cfg.get("llm.n_ctx", 4096),
            verbose=False,
        )

    @staticmethod
    def _native_backend_error(error: OSError) -> str:
        detail = str(error)
        if "access violation" in detail.lower():
            return (
                "llama-cpp-python 原生后端初始化失败 "
                f"({detail})。模型文件已找到，问题更可能是 llama-cpp-python/DLL/CUDA 后端与当前环境不兼容，"
                "不是 models/ 目录缺失。可先改用 CPU 版 llama-cpp-python 或将 llm.n_gpu_layers 设为 0。"
            )
        return f"llama-cpp-python 原生后端加载失败: {detail}"

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
        if cfg.get("llm.subprocess", True) or self._subprocess_mode:
            yield from self._generate_subprocess(prompt, stream=stream)
            return

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

    def _generate_subprocess(self, prompt: str, stream: bool = True) -> Iterator[str]:
        n_gpu_layers = int(cfg.get("llm.n_gpu_layers", 0))
        payload = {
            "prompt": prompt,
            "model_path": str(cfg.abs_path(cfg.get("llm.model_path"))),
            "n_gpu_layers": n_gpu_layers,
            "n_ctx": int(cfg.get("llm.n_ctx", 4096)),
            "max_tokens": int(cfg.get("llm.max_tokens", 512)),
            "temperature": float(cfg.get("llm.temperature", 0.7)),
            "repeat_penalty": float(cfg.get("llm.repeat_penalty", 1.1)),
            "stream": bool(stream),
        }
        env = os.environ.copy()
        if n_gpu_layers <= 0:
            env["CUDA_VISIBLE_DEVICES"] = ""

        proc = subprocess.Popen(
            [sys.executable, "-m", "app.models.llm_subprocess"],
            cwd=str(cfg._BASE),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        diagnostics: list[str] = []
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(payload, ensure_ascii=False))
            proc.stdin.close()

            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    diagnostics.append(line)
                    diagnostics = diagnostics[-20:]
                    continue
                if event.get("type") == "token":
                    token = event.get("text") or ""
                    if token:
                        yield token
                elif event.get("type") == "error":
                    message = str(event.get("message") or "LLM 子进程失败")
                    self._last_error = message
                    raise LLMSubprocessError(message)
        finally:
            try:
                return_code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                return_code = proc.wait(timeout=5)

        if return_code != 0:
            detail = "\n".join(diagnostics[-8:]).strip()
            message = (
                f"LLM 子进程异常退出(exit={return_code})。"
                "这通常是 llama-cpp-python/DLL/CUDA 后端与当前 Windows 环境不兼容；"
                "主程序已保护不崩溃。"
            )
            if detail:
                message += f"\n{detail}"
            self._last_error = message
            raise LLMSubprocessError(message)
