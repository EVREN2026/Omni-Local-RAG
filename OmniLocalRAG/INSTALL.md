# Omni-Local RAG — Windows Installation Guide

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.10 / 3.11 / 3.12 **(x64)** | Must be 64-bit. Check "Add to PATH" during setup |
| Windows 10 / 11 (x64) | — |
| 8 GB+ RAM | 16 GB recommended |
| Disk space | ~3 GB models + ~3 GB dependencies |
| CUDA users only | NVIDIA GPU + driver ≥ 520, CUDA ≥ 11.8 |

---

## Quick Start

### Step 1 — Choose your install script

| Script | When to use |
|--------|-------------|
| **`install.bat`** | Launcher — asks CPU or CUDA, then calls the right script |
| **`install_cpu.bat`** | No GPU / integrated graphics / no CUDA drivers |
| **`install_cuda.bat`** | NVIDIA GPU with CUDA drivers (auto-detects CUDA version) |

Double-click the script that matches your hardware. No Visual C++ Build Tools required.

### Step 2 — Place model files

| Model | Path | Download |
|-------|------|----------|
| Gemma-2-2B-IT Q4_K_M | `models\gemma-2-2b-it-q4_k_m.gguf` | [HuggingFace ↗](https://huggingface.co/bartowski/gemma-2-2b-it-GGUF) |
| BGE-M3 | `models\bge-m3\` | [HuggingFace ↗](https://huggingface.co/BAAI/bge-m3) |

Download BGE-M3 with one command:
```bat
python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-m3', local_dir='models/bge-m3')"
```

### Step 3 — Launch

```bat
python main.py
```

---

## What each script installs

### `install_cpu.bat`

```
pip install torch torchvision torchaudio
    --index-url https://download.pytorch.org/whl/cpu          ← CPU-only PyTorch

pip install llama-cpp-python==0.3.9 --prefer-binary           ← CPU prebuilt wheel

pip install -r requirements.txt                               ← Everything else

config.json  →  n_gpu_layers=0 | embed.device=cpu
```

### `install_cuda.bat`

Auto-detects CUDA version from `nvidia-smi`, then:

| Detected CUDA | PyTorch index | llama-cpp wheel |
|---------------|--------------|-----------------|
| 11.x | `cu118` | `cu118` |
| 12.0 – 12.3 | `cu121` | `cu121` |
| 12.4+ | `cu124` | `cu124` |

```
pip install torch torchvision torchaudio
    --index-url https://download.pytorch.org/whl/<CUDA_TAG>   ← GPU PyTorch

pip install llama-cpp-python==0.3.9 --prefer-binary
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/<CUDA_TAG>

pip install -r requirements.txt

config.json  →  n_gpu_layers=999 | embed.device=cuda
              (999 = offload all layers that fit in VRAM)
```

---

## Tuning GPU layers (CUDA users)

Edit `config.json` after installation:

```json
"llm": {
    "n_gpu_layers": 999
}
```

| Value | Effect |
|-------|--------|
| `0` | CPU only — slowest, lowest VRAM |
| `20` | Partial offload — splits compute between CPU and GPU |
| `999` | Full offload — fastest, needs ~1.8 GB VRAM for Gemma-2-2B Q4 |

---

## Manual install commands

### CPU

```bat
python -m pip install --upgrade pip
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install "llama-cpp-python==0.3.9" --prefer-binary
pip install -r requirements.txt
```

### CUDA 12.1

```bat
python -m pip install --upgrade pip
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install "llama-cpp-python==0.3.9" --prefer-binary --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121
pip install -r requirements.txt
```

---

## Common errors

### `WinError 1114` on `c10.dll` or `onnxruntime_pybind11_state`

GPU build of PyTorch or onnxruntime loaded without CUDA drivers present.

**Fix:** Run `install_cpu.bat`, or reinstall torch manually:
```bat
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

### `Failed building wheel for llama-cpp-python`

No prebuilt wheel for your Python version.

**Fix:** Confirm Python is 3.10 / 3.11 / 3.12 **(x64)**:
```bat
python --version
python -c "import platform; print(platform.architecture())"
```

### `Failed building wheel for chroma-hnswlib`

Using `chromadb < 0.5.0` which requires MSVC.

**Fix:** Confirm `requirements.txt` has `chromadb==0.5.23` or later.

---

## Virtual environment (recommended)

```bat
python -m venv .venv
.venv\Scripts\activate
REM then run install_cpu.bat or install_cuda.bat
```
