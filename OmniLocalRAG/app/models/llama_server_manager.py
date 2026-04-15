"""
llama_server_manager.py — 管理本地 llama-server.exe HTTP 服务进程。

职责：
  • 定位 llama-server 二进制（优先用 config 中配置的路径，其次 models/ 目录下的捆绑包，
    最后尝试 PATH）
  • 用指定 GGUF + 参数启动子进程，并将 stderr 转入日志
  • 健康检测（GET /health）直到就绪或超时
  • 优雅停止 / 强杀
  • 提供 base_url 供 llm_http_client 使用

单例，线程安全（使用 threading.Lock）。
"""

from __future__ import annotations

import os
import platform
import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from app.utils import config as cfg
from app.utils.logger import logger

# ── 常量 ──────────────────────────────────────────────────────────────────────

_HEALTH_TIMEOUT = 180       # 等待服务就绪的最长秒数（大模型加载需要 60-120s）
_HEALTH_INTERVAL = 0.5      # 健康检测轮询间隔（秒）
_STOP_GRACE = 5             # 优雅停止等待秒数

# 捆绑在项目中的 CUDA 12.4 版本目录（相对项目根）
_BUNDLED_CUDA_DIR = "models/llama-b8747-bin-win-cuda-12.4-x64"

# GitHub Releases 下载基础 URL（用于自动下载）
_GH_RELEASES_BASE = (
    "https://github.com/ggml-org/llama.cpp/releases/latest/download"
)

_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _is_windows() -> bool:
    return platform.system() == "Windows"


def _server_exe_name() -> str:
    return "llama-server.exe" if _is_windows() else "llama-server"


def _detect_cuda() -> bool:
    """粗略检测当前机器是否有可用的 NVIDIA CUDA GPU。"""
    # 1) 尝试 nvidia-smi
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return True
    except Exception:
        pass

    # 2) 尝试 PyTorch（如果已安装）
    try:
        import torch  # type: ignore
        return bool(torch.cuda.is_available())
    except ImportError:
        pass

    # 3) Windows: 检测 nvml.dll / CUDA DLL
    if _is_windows():
        cuda_paths = [
            r"C:\Windows\System32\nvcuda.dll",
            r"C:\Windows\System32\nvml.dll",
        ]
        if any(Path(p).exists() for p in cuda_paths):
            return True

    return False


def _find_bundled_server() -> Optional[Path]:
    """查找项目内捆绑的 llama-server 二进制。"""
    base = cfg._BASE
    exe = _server_exe_name()

    # 1) 用户在 config 里指定的目录
    server_dir = cfg.get("llama_server.server_dir", "")
    if server_dir:
        p = cfg.abs_path(server_dir) / exe
        if p.exists():
            return p

    # 2) 项目自带 CUDA 版本（已捆绑）
    bundled = base / _BUNDLED_CUDA_DIR / exe
    if bundled.exists():
        return bundled

    # 3) PATH 里的 llama-server
    found = shutil.which(exe)
    if found:
        return Path(found)

    return None


def _download_llama_server(dest_dir: Path, use_cuda: bool) -> Optional[Path]:
    """
    从 GitHub Releases 下载 llama-server 压缩包并解压到 dest_dir。
    Windows only；Linux/macOS 需要自行编译。
    返回解压后的 exe 路径，失败返回 None。
    """
    if not _is_windows():
        logger.warning("自动下载仅支持 Windows，请手动编译 llama.cpp")
        return None

    import io
    import zipfile

    # 选择合适的 Release 资产名（参考 llama.cpp Release 命名规则）
    if use_cuda:
        asset = "llama-b{build}-bin-win-cuda-12.4-x64.zip"
    else:
        asset = "llama-b{build}-bin-win-noavx-x64.zip"

    # 先获取最新 Release 的 tag
    api_url = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "OmniLocalRAG"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            import json
            info = json.loads(resp.read().decode())
        tag = info.get("tag_name", "b9999")
        build_num = tag.lstrip("b")
    except Exception as e:
        logger.error(f"获取 llama.cpp 最新版本失败: {e}")
        return None

    # 在 assets 列表里找匹配的文件
    assets = info.get("assets", [])
    download_url = None
    keyword = "cuda-12.4" if use_cuda else "noavx"
    for a in assets:
        name: str = a.get("name", "")
        if "win" in name and keyword in name and name.endswith(".zip"):
            download_url = a.get("browser_download_url")
            break

    if not download_url:
        # fallback: 尝试直接拼 URL
        asset_name = f"llama-{tag}-bin-win-{'cuda-12.4' if use_cuda else 'noavx'}-x64.zip"
        download_url = f"https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{asset_name}"

    logger.info(f"正在下载 llama-server ({tag}): {download_url}")
    try:
        req = urllib.request.Request(download_url, headers={"User-Agent": "OmniLocalRAG"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        dest_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(dest_dir)
        exe = dest_dir / _server_exe_name()
        if exe.exists():
            if not _is_windows():
                exe.chmod(0o755)
            logger.info(f"llama-server 下载解压完成: {exe}")
            return exe
        # 有些 zip 里有子目录，递归查找
        for p in dest_dir.rglob(_server_exe_name()):
            logger.info(f"llama-server 找到: {p}")
            return p
    except Exception as e:
        logger.error(f"下载 llama-server 失败: {e}", exc_info=True)

    return None


# ── 主类 ──────────────────────────────────────────────────────────────────────

class LlamaServerManager:
    """单例：管理 llama-server.exe 子进程生命周期。"""

    _instance: Optional["LlamaServerManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "LlamaServerManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._proc: Optional[subprocess.Popen] = None
            cls._instance._started = False
            cls._instance._last_error = ""
            cls._instance._current_model: Optional[str] = None
            cls._instance._log_thread: Optional[threading.Thread] = None
        return cls._instance

    # ── 公共属性 ─────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True
        return self.health_check()

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def base_url(self) -> str:
        port = int(cfg.get("llama_server.port", 8000))
        host = cfg.get("llama_server.host", "127.0.0.1")
        return f"http://{host}:{port}"

    @property
    def current_model(self) -> Optional[str]:
        return self._current_model

    # ── 公共方法 ─────────────────────────────────────────────────────────

    def ensure_started(self, model_path: Optional[str] = None) -> bool:
        """
        确保 llama-server 以指定模型运行。
        如果已在运行且模型一致则直接返回 True。
        如果模型不同则先停止再重启。
        """
        target_model = model_path or str(
            cfg.abs_path(cfg.get("llama_server.model_path", cfg.get("llm.model_path", "")))
        )

        with self._lock:
            # 已在运行且模型相同
            if self._proc is not None and self._proc.poll() is None:
                if self._current_model == target_model:
                    return True
                # 模型不同，需要重启
                self._stop_locked()

            # A previous app run or a manual launch may already own the port.
            # Reuse it when it is healthy instead of starting a duplicate
            # process that would fail with "address already in use".
            if self.health_check():
                remote_props = self._remote_props() or {}
                remote_model = str(remote_props.get("model_path", "") or "") or None
                if remote_model and not self._same_path(remote_model, target_model):
                    self._last_error = (
                        "A llama-server is already running, but its model differs from the current config: "
                        f"{remote_model} != {target_model}"
                    )
                    logger.error(self._last_error)
                    return False
                remote_ctx = (
                    remote_props.get("default_generation_settings", {})
                    .get("n_ctx")
                )
                build_info = str(remote_props.get("build_info", "") or "")
                self._current_model = target_model
                logger.warning(
                    "Reusing running llama-server: "
                    f"{self.base_url} "
                    f"(build={build_info or 'unknown'}, "
                    f"model={remote_model or 'unknown'}, "
                    f"n_ctx={remote_ctx if remote_ctx is not None else 'unknown'})"
                )
                return True

            return self._start_locked(target_model)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def health_check(self) -> bool:
        """向 /health 发一个 HTTP GET，返回是否 200 OK。"""
        try:
            url = f"{self.base_url}/health"
            req = urllib.request.Request(url)
            with _LOCAL_OPENER.open(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _remote_model_path(self) -> Optional[str]:
        """Read the model path from an already-running llama-server."""
        props = self._remote_props()
        if not props:
            return None
        model_path = str(props.get("model_path", "") or "")
        return model_path or None

    def _remote_props(self) -> Optional[dict]:
        """Read /props from an already-running llama-server."""
        try:
            req = urllib.request.Request(f"{self.base_url}/props")
            with _LOCAL_OPENER.open(req, timeout=3) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            return None

    @staticmethod
    def _same_path(left: str, right: str) -> bool:
        try:
            return Path(left).resolve() == Path(right).resolve()
        except Exception:
            return left == right

    # ── 内部方法（必须在 _lock 持有时调用） ──────────────────────────────

    def _start_locked(self, model_path: str) -> bool:
        self._last_error = ""

        # 1) 找到 llama-server 可执行文件
        exe = self._resolve_exe()
        if exe is None:
            self._last_error = (
                "找不到 llama-server 可执行文件。\n"
                "请将二进制放到 models/llama-b8747-bin-win-cuda-12.4-x64/ 目录，"
                "或在 config.json 的 llama_server.server_dir 中指定路径，"
                "或允许程序自动从 GitHub 下载（llama_server.auto_download=true）。"
            )
            logger.error(self._last_error)
            return False

        # 2) 验证模型文件
        if not Path(model_path).exists():
            self._last_error = f"GGUF 模型文件不存在: {model_path}"
            logger.error(self._last_error)
            return False

        # 3) 组装启动命令
        cmd = self._build_cmd(exe, model_path)
        logger.info(f"启动 llama-server: {' '.join(str(c) for c in cmd)}")

        # 4) 启动子进程
        try:
            env = os.environ.copy()
            # 确保 DLL 与 exe 同目录可以被找到（Windows）
            exe_dir = str(exe.parent)
            path_var = env.get("PATH", "")
            if exe_dir not in path_var:
                env["PATH"] = exe_dir + os.pathsep + path_var

            self._proc = subprocess.Popen(
                cmd,
                cwd=str(exe.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except Exception as e:
            self._last_error = f"llama-server 启动失败: {e}"
            logger.error(self._last_error, exc_info=True)
            return False

        self._current_model = model_path

        # 5) 启动日志转发线程（在锁外运行，但这里只是创建线程对象）
        self._log_thread = threading.Thread(
            target=self._pipe_logs,
            args=(self._proc,),
            daemon=True,
            name="llama-server-log",
        )
        self._log_thread.start()

        # 6) 等待健康检测通过
        return self._wait_healthy()

    def _stop_locked(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        self._current_model = None
        try:
            # On Windows, terminate the entire process tree to avoid orphans
            if _is_windows():
                subprocess.call(
                    ["taskkill", "/f", "/t", "/pid", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.wait(timeout=_STOP_GRACE)
            else:
                proc.terminate()
                proc.wait(timeout=_STOP_GRACE)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        except Exception as e:
            logger.warning(f"llama-server 停止时出错: {e}")
        logger.info("llama-server 已停止")

    def _resolve_exe(self) -> Optional[Path]:
        """按优先级查找 llama-server 可执行文件，找不到则尝试下载。"""
        exe = _find_bundled_server()
        if exe:
            return exe

        if cfg.get("llama_server.auto_download", True):
            use_cuda = _detect_cuda()
            logger.info(f"未找到 llama-server，尝试自动下载（CUDA={use_cuda}）...")
            dest_dir = cfg.abs_path("models/llama-server-auto")
            exe = _download_llama_server(dest_dir, use_cuda=use_cuda)
            if exe:
                return exe

        return None

    def _build_cmd(self, exe: Path, model_path: str) -> list:
        """根据 config 构造 llama-server 命令行参数列表。"""
        port      = int(cfg.get("llama_server.port",         8000))
        host      = cfg.get("llama_server.host",             "127.0.0.1")
        gpu_layers= int(cfg.get("llama_server.n_gpu_layers",
                                cfg.get("llm.n_gpu_layers", 999)))
        ctx_size  = int(cfg.get("llama_server.ctx_size",
                                cfg.get("llm.n_ctx",       8192)))
        threads   = int(cfg.get("llama_server.threads",      -1))
        parallel  = int(cfg.get("llama_server.parallel",     2))
        batch_size= int(cfg.get("llama_server.batch_size",   1024))
        ubatch    = int(cfg.get("llama_server.ubatch_size",  512))
        flash_attn= bool(cfg.get("llama_server.flash_attn",  True))
        no_mmap   = bool(cfg.get("llama_server.no_mmap",     True))
        numa      = cfg.get("llama_server.numa",             "distribute")
        extra_args= cfg.get("llama_server.extra_args",       "")

        cmd: list = [
            str(exe),
            "-m", str(model_path),
            "--host", host,
            "--port", str(port),
            "--ctx-size", str(ctx_size),
        ]

        if gpu_layers >= 0:
            cmd += ["--n-gpu-layers", str(gpu_layers)]

        if threads > 0:
            cmd += ["--threads", str(threads)]

        if parallel > 0:
            cmd += ["--parallel", str(parallel)]

        if batch_size > 0:
            cmd += ["--batch-size", str(batch_size)]

        if ubatch > 0 and batch_size > 0:
            cmd += ["--ubatch-size", str(min(ubatch, batch_size))]

        if flash_attn:
            cmd += ["--flash-attn", "on"]

        if no_mmap:
            cmd += ["--no-mmap"]

        if numa:
            cmd += ["--numa", numa]

        # 追加用户自定义参数（空格分隔的字符串）
        if extra_args:
            import shlex
            cmd += shlex.split(extra_args)

        return cmd

    def _wait_healthy(self) -> bool:
        """轮询 /health 直到 200 OK 或超时。"""
        timeout = int(cfg.get("llama_server.startup_timeout", _HEALTH_TIMEOUT))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # 子进程意外退出
            if self._proc is None or self._proc.poll() is not None:
                rc = self._proc.poll() if self._proc else -1
                self._last_error = (
                    f"llama-server 意外退出（exit={rc}）。"
                    "请检查日志或模型文件是否正确。"
                )
                logger.error(self._last_error)
                return False

            if self.health_check():
                logger.info(f"llama-server 就绪: {self.base_url}")
                return True

            time.sleep(_HEALTH_INTERVAL)

        self._last_error = (
            f"llama-server 启动超时（>{timeout}s），"
            "可能是模型文件过大或 GPU 显存不足。"
        )
        logger.error(self._last_error)
        return False

    @staticmethod
    def _pipe_logs(proc: subprocess.Popen) -> None:
        """将 llama-server 的 stdout/stderr 转发到应用日志。"""
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                # Key progress lines use INFO so they are visible by default;
                # everything else stays at DEBUG to avoid noise.
                lower = line.lower()
                if any(kw in lower for kw in ("load", "model", "server", "listening", "ready", "error", "warn", "fail", "cuda", "gguf", "ctx")):
                    logger.info(f"[llama-server] {line}")
                else:
                    logger.debug(f"[llama-server] {line}")
        except Exception:
            pass
