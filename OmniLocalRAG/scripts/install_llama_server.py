"""
install_llama_server.py — 检测环境并下载合适的 llama-server 二进制。

用法：
    python scripts/install_llama_server.py [--cpu] [--cuda] [--dest <目录>]

选项：
    --cpu    强制下载 CPU 版本
    --cuda   强制下载 CUDA 版本
    --dest   解压目标目录（默认：models/llama-server-auto）

无参数时自动检测环境：
    - 检测到 NVIDIA GPU → 下载 CUDA 12.4 版本
    - 未检测到       → 下载 CPU（noavx）版本

注：如果 models/llama-b8747-bin-win-cuda-12.4-x64/llama-server.exe 已存在，
此脚本主要用于更新或下载 CPU 备用版本。
"""

from __future__ import annotations

import argparse
import io
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

# 项目根（此脚本在 scripts/ 目录下）
_BASE = Path(__file__).parent.parent


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _detect_cuda() -> bool:
    """检测是否有可用 CUDA GPU。"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=8
        )
        if result.returncode == 0 and result.stdout.strip():
            names = result.stdout.strip().splitlines()
            print(f"  检测到 GPU: {', '.join(names)}")
            return True
    except Exception:
        pass

    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            print(f"  torch 检测到 CUDA GPU: {name}")
            return True
    except ImportError:
        pass

    if _is_windows():
        for dll in [r"C:\Windows\System32\nvcuda.dll",
                    r"C:\Windows\System32\nvml.dll"]:
            if Path(dll).exists():
                print(f"  检测到 CUDA DLL: {dll}")
                return True

    return False


def _get_latest_release() -> tuple[str, list[dict]]:
    """返回 (tag_name, assets_list)。"""
    api_url = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
    print(f"  查询 GitHub Releases: {api_url}")
    req = urllib.request.Request(api_url, headers={"User-Agent": "OmniLocalRAG"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        info = json.loads(resp.read().decode())
    tag = info.get("tag_name", "unknown")
    assets = info.get("assets", [])
    print(f"  最新版本: {tag}  ({len(assets)} 个文件)")
    return tag, assets


def _find_asset_url(assets: list[dict], use_cuda: bool) -> str | None:
    keyword = "cuda-12.4" if use_cuda else "noavx"
    for a in assets:
        name: str = a.get("name", "")
        if "win" in name and keyword in name and name.endswith(".zip"):
            url = a.get("browser_download_url", "")
            print(f"  找到资产: {name}")
            return url
    return None


def _download_and_extract(url: str, dest: Path) -> Path | None:
    print(f"  下载: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "OmniLocalRAG"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            data = b""
            chunk_size = 1024 * 64
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                data += chunk
                if total:
                    pct = len(data) * 100 // total
                    print(f"\r  已下载 {len(data)//1024//1024}MB / {total//1024//1024}MB ({pct}%)", end="")
            print()
    except Exception as e:
        print(f"  下载失败: {e}", file=sys.stderr)
        return None

    dest.mkdir(parents=True, exist_ok=True)
    print(f"  解压到: {dest}")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(dest)
    except Exception as e:
        print(f"  解压失败: {e}", file=sys.stderr)
        return None

    exe_name = "llama-server.exe" if _is_windows() else "llama-server"
    # 直接检查
    direct = dest / exe_name
    if direct.exists():
        return direct
    # 子目录查找
    for p in dest.rglob(exe_name):
        return p

    print(f"  警告: 解压后未找到 {exe_name}", file=sys.stderr)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 llama-server 二进制")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--cpu",  action="store_true", help="强制下载 CPU 版本")
    group.add_argument("--cuda", action="store_true", help="强制下载 CUDA 12.4 版本")
    parser.add_argument(
        "--dest", default=str(_BASE / "models" / "llama-server-auto"),
        help="解压目标目录"
    )
    args = parser.parse_args()

    # ── 1. 确定版本 ──
    if args.cpu:
        use_cuda = False
        print("[1] 强制 CPU 模式")
    elif args.cuda:
        use_cuda = True
        print("[1] 强制 CUDA 模式")
    else:
        print("[1] 自动检测环境...")
        use_cuda = _detect_cuda()
        print(f"  → {'CUDA' if use_cuda else 'CPU'} 模式")

    # ── 2. 检查捆绑版本 ──
    bundled_dir = _BASE / "models" / "llama-b8747-bin-win-cuda-12.4-x64"
    bundled_exe = bundled_dir / ("llama-server.exe" if _is_windows() else "llama-server")
    if bundled_exe.exists() and use_cuda:
        print(f"\n[2] 检测到已捆绑的 CUDA 版本: {bundled_exe}")
        print("  无需下载，如需更新请使用 --dest 指定新目录后重新运行。")
        return 0

    # ── 3. 从 GitHub 下载 ──
    dest = Path(args.dest)
    print(f"\n[2] 从 GitHub 下载 llama-server ({'CUDA 12.4' if use_cuda else 'CPU'}) → {dest}")

    try:
        tag, assets = _get_latest_release()
    except Exception as e:
        print(f"  获取 Release 信息失败: {e}", file=sys.stderr)
        return 1

    url = _find_asset_url(assets, use_cuda)
    if not url:
        # fallback 拼 URL
        keyword = "cuda-12.4" if use_cuda else "noavx"
        url = (
            f"https://github.com/ggml-org/llama.cpp/releases/download/"
            f"{tag}/llama-{tag}-bin-win-{keyword}-x64.zip"
        )
        print(f"  未在 assets 中找到匹配项，尝试直接拼 URL: {url}")

    exe = _download_and_extract(url, dest)
    if exe is None:
        print("\n下载或解压失败，请手动下载：", file=sys.stderr)
        print(f"  {url}", file=sys.stderr)
        return 1

    print(f"\n[3] 完成！llama-server 路径: {exe}")
    print(
        "\n提示：在 config.json 的 llama_server.server_dir 中填入：\n"
        f"  {exe.parent.relative_to(_BASE)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
