@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"

echo.
echo ============================================================
echo   Omni-Local RAG  --  Repair PyTorch CUDA Runtime
echo ============================================================
echo.

set "PY=python"
if exist "%CD%\venv\Scripts\python.exe" set "PY=%CD%\venv\Scripts\python.exe"

"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Run install.bat first or activate Python 3.12 x64.
    pause & exit /b 1
)

echo [1/4] Detecting NVIDIA CUDA driver ...
for /f "delims=" %%c in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$line=(nvidia-smi | Select-String -Pattern 'CUDA Version' | Select-Object -First 1).Line; if ($line -match 'CUDA Version:\s*([0-9]+(?:\.[0-9]+)?)') { $matches[1] }"') do set "CUDA_VER_FULL=%%c"
if "%CUDA_VER_FULL%"=="" (
    echo [ERROR] Could not detect CUDA version from nvidia-smi.
    pause & exit /b 1
)
for /f "tokens=1,2 delims=." %%m in ("%CUDA_VER_FULL%") do (
    set "CUDA_MAJOR=%%m"
    set "CUDA_MINOR=%%n"
)
if not defined CUDA_MINOR set "CUDA_MINOR=0"
if "%CUDA_MAJOR%"=="13" (
    set "CUDA_TAG=cu128"
) else if "%CUDA_MAJOR%"=="12" (
    if %CUDA_MINOR% GEQ 8 (
        set "CUDA_TAG=cu128"
    ) else if %CUDA_MINOR% GEQ 6 (
        set "CUDA_TAG=cu126"
    ) else (
        echo [ERROR] Detected CUDA %CUDA_VER_FULL%. Update NVIDIA driver or use CPU mode.
        pause & exit /b 1
    )
) else (
    echo [ERROR] Detected CUDA %CUDA_VER_FULL%. Update NVIDIA driver or use CPU mode.
    pause & exit /b 1
)
echo [OK] Detected CUDA %CUDA_VER_FULL%; reinstalling matching %CUDA_TAG% wheels

echo.
echo [2/4] Reinstalling torch / torchvision / torchaudio ...
"%PY%" -m pip uninstall -y torch torchvision torchaudio >nul 2>&1
"%PY%" -m pip install --no-cache-dir --force-reinstall ^
    torch==2.11.0+%CUDA_TAG% torchvision==0.26.0+%CUDA_TAG% torchaudio==2.11.0+%CUDA_TAG% ^
    --index-url https://download.pytorch.org/whl/%CUDA_TAG%
if errorlevel 1 (
    echo [ERROR] Failed to reinstall CUDA PyTorch wheels.
    pause & exit /b 1
)

echo.
echo [3/4] Setting config.json embed.device=cuda ...
"%PY%" -c "import json,pathlib; p=pathlib.Path('config.json'); c=json.loads(p.read_text(encoding='utf-8')); c.setdefault('embed',{})['device']='cuda'; p.write_text(json.dumps(c,indent=2,ensure_ascii=False), encoding='utf-8'); print('[OK] config.json updated')"
if errorlevel 1 (
    echo [ERROR] Failed to update config.json.
    pause & exit /b 1
)

echo.
echo [4/4] Import smoke test ...
"%PY%" -c "import torch, torchvision; print('  torch', torch.__version__, '| cuda:', torch.version.cuda, '| available:', torch.cuda.is_available()); print('  torchvision', torchvision.__version__)"
if errorlevel 1 (
    echo [ERROR] torch / torchvision still cannot be imported together.
    pause & exit /b 1
)
"%PY%" -c "from sentence_transformers import SentenceTransformer; print('  sentence-transformers OK')"
if errorlevel 1 (
    echo [ERROR] sentence-transformers import still failed.
    pause & exit /b 1
)

echo.
echo ============================================================
echo   [DONE] CUDA PyTorch runtime repaired.
echo ============================================================
pause & exit /b 0
