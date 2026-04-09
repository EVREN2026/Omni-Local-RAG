@echo off
setlocal EnableDelayedExpansion
echo.
echo ============================================================
echo   Omni-Local RAG  --  CUDA GPU Installation
echo   For users with NVIDIA GPU + CUDA drivers installed
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

REM ---------- NVIDIA GPU / CUDA check ------------------------------
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo [ERROR] nvidia-smi not found. No NVIDIA driver detected.
    echo         Use install_cpu.bat instead, or install NVIDIA drivers first.
    pause & exit /b 1
)
echo [OK] NVIDIA GPU detected

REM -- Parse CUDA version from nvidia-smi (format: "CUDA Version: 12.4")
for /f "tokens=3" %%c in ('nvidia-smi ^| findstr /i "CUDA Version"') do set CUDA_VER_FULL=%%c
if "%CUDA_VER_FULL%"=="" (
    echo [WARN] Could not detect CUDA version, defaulting to CUDA 12.1
    set CUDA_TAG=cu121
    goto :cuda_tag_set
)

REM -- Extract major version (e.g. "12.4" -> "12")
for /f "delims=." %%m in ("%CUDA_VER_FULL%") do set CUDA_MAJOR=%%m
REM -- Extract minor version (e.g. "12.4" -> "4")
for /f "tokens=2 delims=." %%n in ("%CUDA_VER_FULL%") do set CUDA_MINOR=%%n

echo [OK] Detected CUDA %CUDA_VER_FULL%

REM -- Map CUDA version to PyTorch/llama-cpp wheel tag
REM    Supported: 11.8 -> cu118 | 12.1/12.2/12.3 -> cu121 | 12.4+ -> cu124
if "%CUDA_MAJOR%"=="11" (
    set CUDA_TAG=cu118
    goto :cuda_tag_set
)
if "%CUDA_MAJOR%"=="12" (
    if %CUDA_MINOR% LEQ 3 (
        set CUDA_TAG=cu121
    ) else (
        set CUDA_TAG=cu124
    )
    goto :cuda_tag_set
)
REM Fallback for unexpected versions
set CUDA_TAG=cu121
echo [WARN] Unrecognized CUDA major version %CUDA_MAJOR%, using cu121 as fallback

:cuda_tag_set
echo [OK] Using wheel tag: %CUDA_TAG%

REM ---------- Step 1: Upgrade pip ----------------------------------
echo.
echo [1/5] Upgrading pip ...
python -m pip install --upgrade pip --quiet
if errorlevel 1 goto :error
echo [OK] pip upgraded

REM ---------- Step 2: PyTorch CUDA wheel ---------------------------
echo.
echo [2/5] Installing PyTorch %CUDA_TAG% build ...
pip install torch torchvision torchaudio ^
    --index-url https://download.pytorch.org/whl/%CUDA_TAG% ^
    --quiet
if errorlevel 1 goto :error
echo [OK] PyTorch %CUDA_TAG% installed

REM ---------- Step 3: llama-cpp-python CUDA prebuilt ---------------
echo.
echo [3/5] Installing llama-cpp-python %CUDA_TAG% prebuilt wheel ...
pip install "llama-cpp-python==0.3.9" --prefer-binary --quiet ^
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/%CUDA_TAG%
if errorlevel 1 (
    echo       Prebuilt CUDA wheel unavailable, falling back to CPU wheel ...
    echo       (You can still run the model; GPU layers will be 0)
    pip install "llama-cpp-python==0.3.9" --prefer-binary --quiet
    if errorlevel 1 goto :llama_error
    set CUDA_TAG=cpu_fallback
)
echo [OK] llama-cpp-python installed

REM ---------- Step 4: Remaining dependencies -----------------------
echo.
echo [4/5] Installing remaining dependencies ...
pip install -r requirements.txt --quiet
if errorlevel 1 goto :error
echo [OK] All dependencies installed

REM ---------- Step 5: Write CUDA config ----------------------------
echo.
echo [5/5] Writing CUDA config ...
if "%CUDA_TAG%"=="cpu_fallback" (
    echo       llama-cpp fell back to CPU wheel -- setting n_gpu_layers=0
    python -c "import json,pathlib; p=pathlib.Path('config.json'); c=json.loads(p.read_text()); c['llm']['n_gpu_layers']=0; c['embed']['device']='cuda'; p.write_text(json.dumps(c,indent=2,ensure_ascii=False)); print('[OK] config.json: embed=cuda, llm=cpu')"
) else (
    REM n_gpu_layers=999 -> llama-cpp offloads ALL layers that fit in VRAM
    python -c "import json,pathlib; p=pathlib.Path('config.json'); c=json.loads(p.read_text()); c['llm']['n_gpu_layers']=999; c['embed']['device']='cuda'; p.write_text(json.dumps(c,indent=2,ensure_ascii=False)); print('[OK] config.json: embed=cuda, llm n_gpu_layers=999 (full offload)')"
)
if errorlevel 1 goto :error

REM ---------- Smoke test ------------------------------------------
echo.
echo --- Import smoke test ------------------------------------------
python -c "import torch; print('  torch', torch.__version__, '| CUDA available:', torch.cuda.is_available())"
python -c "import torch; print('  GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"
python -c "import chromadb;              print('  chromadb          OK')"
python -c "import llama_cpp;             print('  llama-cpp-python  OK')"
python -c "import sentence_transformers; print('  sentence-transformers OK')"
python -c "import PyQt5;                 print('  PyQt5             OK')"

echo.
echo ============================================================
echo   [DONE] CUDA installation complete  (%CUDA_TAG%)
echo   Place model files in models\  then run: python main.py
echo   Tip: adjust n_gpu_layers in config.json to tune VRAM usage
echo        999 = full offload  |  0 = CPU only  |  e.g. 20 = partial
echo ============================================================
pause & exit /b 0

:llama_error
echo.
echo [ERROR] Could not install llama-cpp-python.
echo         Ensure Python 3.10 / 3.11 / 3.12 (x64) and internet access.
pause & exit /b 1

:error
echo.
echo [ERROR] Installation failed. Check the output above or see INSTALL.md.
pause & exit /b 1
