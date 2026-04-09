@echo off
setlocal EnableDelayedExpansion
echo.
echo ============================================================
echo   Omni-Local RAG  --  CPU-only Installation
echo   For users without a dedicated GPU or CUDA drivers
echo ============================================================
echo.

REM ---------- Python check -----------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10/3.11/3.12 (x64)
    echo         https://www.python.org/downloads/
    pause & exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [OK] Python %PY_VER%

REM ---------- Step 1: Upgrade pip ----------------------------------
echo.
echo [1/5] Upgrading pip ...
python -m pip install --upgrade pip --quiet
if errorlevel 1 goto :error
echo [OK] pip upgraded

REM ---------- Step 2: PyTorch CPU wheel ----------------------------
echo.
echo [2/5] Installing PyTorch CPU build ...
echo       (GPU build causes WinError 1114 on c10.dll without CUDA drivers)
pip install torch torchvision torchaudio ^
    --index-url https://download.pytorch.org/whl/cpu ^
    --quiet
if errorlevel 1 goto :error
echo [OK] PyTorch CPU installed

REM ---------- Step 3: llama-cpp-python CPU prebuilt ----------------
echo.
echo [3/5] Installing llama-cpp-python CPU prebuilt wheel ...
pip install "llama-cpp-python==0.3.9" --prefer-binary --quiet
if errorlevel 1 (
    echo       Primary source failed, trying fallback index ...
    pip install "llama-cpp-python==0.3.9" --prefer-binary --quiet ^
        --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
    if errorlevel 1 goto :llama_error
)
echo [OK] llama-cpp-python installed

REM ---------- Step 4: Remaining dependencies -----------------------
echo.
echo [4/6] Installing remaining dependencies ...
pip install -r requirements.txt --quiet
if errorlevel 1 goto :error
echo [OK] All dependencies installed

REM ---------- Step 4b: Fix hnswlib (replace with chroma-hnswlib) ---
REM   hnswlib prebuilt wheels on PyPI may use AVX-512 instructions
REM   that cause "Windows fatal exception: access violation" at import.
REM   chroma-hnswlib is chromadb's own fork compiled without AVX-512.
echo.
echo [5/6] Replacing hnswlib with chroma-hnswlib (AVX crash fix) ...
pip uninstall hnswlib -y --quiet
pip install chroma-hnswlib --quiet
if errorlevel 1 (
    echo [WARN] chroma-hnswlib install failed, keeping original hnswlib
)
echo [OK] hnswlib replaced

REM ---------- Step 5: Write CPU config ----------------------------
echo.
echo [6/6] Writing CPU config  (n_gpu_layers=0, embed.device=cpu) ...
python -c "import json,pathlib; p=pathlib.Path('config.json'); c=json.loads(p.read_text()); c['llm']['n_gpu_layers']=0; c['embed']['device']='cpu'; p.write_text(json.dumps(c,indent=2,ensure_ascii=False)); print('[OK] config.json updated')"
if errorlevel 1 goto :error

REM ---------- Smoke test ------------------------------------------
echo.
echo --- Import smoke test ------------------------------------------
python -c "import torch; print('  torch', torch.__version__, '| CUDA available:', torch.cuda.is_available())"
python -c "import chromadb;              print('  chromadb          OK')"
python -c "import llama_cpp;             print('  llama-cpp-python  OK')"
python -c "import sentence_transformers; print('  sentence-transformers OK')"
python -c "import PyQt5;                 print('  PyQt5             OK')"

echo.
echo ============================================================
echo   [DONE] CPU installation complete.
echo   Place model files in models\  then run: python main.py
echo ============================================================
pause & exit /b 0

:llama_error
echo.
echo [ERROR] No prebuilt llama-cpp-python wheel for this Python version.
echo         Ensure you are using Python 3.10 / 3.11 / 3.12 (x64).
pause & exit /b 1

:error
echo.
echo [ERROR] Installation failed. Check the output above or see INSTALL.md.
pause & exit /b 1
