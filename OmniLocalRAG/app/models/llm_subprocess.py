"""Command-line llama-cpp runner used to isolate native backend crashes."""

import faulthandler
import json
import os
import sys


def _emit(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _native_backend_error(error: OSError) -> str:
    detail = str(error)
    if "access violation" in detail.lower():
        return (
            "llama-cpp-python 原生后端初始化失败 "
            f"({detail})。模型文件已找到，问题更可能是 llama-cpp-python/DLL/CUDA 后端与当前环境不兼容，"
            "不是 models/ 目录缺失。可先改用 CPU 版 llama-cpp-python 或将 llm.n_gpu_layers 设为 0。"
        )
    return f"llama-cpp-python 原生后端加载失败: {detail}"


def main() -> int:
    # Native crashes should stay inside this child process. The parent receives
    # the non-zero exit code and keeps the Qt UI alive.
    try:
        faulthandler.disable()
    except Exception:
        pass
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    try:
        payload = json.loads(sys.stdin.read() or "{}")
        n_gpu_layers = int(payload.get("n_gpu_layers", 0))
        if n_gpu_layers <= 0:
            os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

        from llama_cpp import Llama  # type: ignore

        llm = Llama(
            model_path=str(payload["model_path"]),
            n_gpu_layers=n_gpu_layers,
            n_ctx=int(payload.get("n_ctx", 4096)),
            verbose=False,
        )
        params = {
            "max_tokens": int(payload.get("max_tokens", 512)),
            "temperature": float(payload.get("temperature", 0.7)),
            "repeat_penalty": float(payload.get("repeat_penalty", 1.1)),
            "stream": bool(payload.get("stream", True)),
        }
        prompt = str(payload.get("prompt") or "")
        if params["stream"]:
            for chunk in llm(prompt, **params):
                token = chunk["choices"][0]["text"]
                if token:
                    _emit({"type": "token", "text": token})
        else:
            result = llm(prompt, **params)
            _emit({"type": "token", "text": result["choices"][0]["text"]})
        _emit({"type": "done"})
        return 0
    except ImportError as exc:
        _emit({"type": "error", "message": f"llama-cpp-python 未安装或无法导入: {exc}"})
        return 2
    except OSError as exc:
        _emit({"type": "error", "message": _native_backend_error(exc)})
        return 3
    except Exception as exc:
        _emit({"type": "error", "message": f"LLM 子进程生成失败: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
