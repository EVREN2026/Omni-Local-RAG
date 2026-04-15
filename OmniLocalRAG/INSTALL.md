# Omni-Local RAG Installation (Windows)

## Runtime architecture
- LLM chat generation uses `llama-server` (HTTP service).
- Python does **not** use `llama-cpp-python` bindings.
- Embedding uses `sentence-transformers` + PyTorch.

## Prerequisites
- Windows 10/11 x64
- Python 3.12 x64
- For CUDA mode: NVIDIA driver with CUDA runtime support

## One-click install
- `install.bat`: choose CPU or CUDA
- `install_cpu.bat`: force CPU mode
- `install_cuda.bat`: force CUDA mode

Both scripts create/use local `venv`, install dependencies, and run smoke checks.

## Model files
Place model files before first run:
- LLM GGUF: `models\gemma-4-2b-it-q4_k_m.gguf`
- BGE-M3: `models\bge-m3\`

The app starts local `llama-server` from:
- `models\llama-b8747-bin-win-cuda-12.4-x64\llama-server.exe`

If this binary is missing, configure `llama_server.server_dir` in `config.json`.

## Launch
```bat
venv\Scripts\python.exe main.py
```

## Quick checks
```bat
venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"
venv\Scripts\python.exe -c "from app.models.llama_server_manager import _find_bundled_server; print(_find_bundled_server())"
```

## Troubleshooting
### CUDA requested but unavailable
- Check NVIDIA driver and `nvidia-smi`.
- Re-run `install_cuda.bat`.

### Embedding import fails with torchvision/torch mismatch
- Reinstall matching torch stack (same index/tag for torch/torchvision/torchaudio).
- Re-run install script to recover a clean set.

### llama-server failed to start
- Confirm `llama-server.exe` path and GGUF model path in `config.json`.
- Run `repair_llama_cpu.bat` (legacy filename; now checks llama-server runtime).