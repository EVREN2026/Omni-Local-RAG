@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"

echo.
echo ============================================================
echo   Omni-Local RAG  --  Repair PyTorch CPU Runtime
echo ============================================================
echo.

REM ---------- Python check -----------------------------------------
set PY=python
python --version >nul 2>&1
if errorlevel 1 (
    set PY=py
    py --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python not found. Install Python 3.10/3.11/3.12 or activate venv.
        pause & exit /b 1
    )
)

for /f "tokens=2" %%v in ('%PY% --version 2^>^&1') do set PY_VER=%%v
echo [OK] Python %PY_VER% ^(via %PY%^)

REM ---------- Remove broken native wheels --------------------------
echo.
echo [1/3] Removing existing torch packages ...
%PY% -m pip uninstall -y torch torchvision torchaudio >nul 2>&1

REM ---------- Install CPU wheels -----------------------------------
echo.
echo [2/3] Installing CPU-only PyTorch wheels ...
%PY% -m pip install --no-cache-dir --force-reinstall torch torchvision torchaudio ^
    --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to install CPU-only PyTorch.
    echo         Check network access and Python version.
    pause & exit /b 1
)

REM ---------- Force CPU embedding config ----------------------------
echo.
echo [3/3] Setting config.json embed.device=cpu ...
%PY% -c "import json,pathlib; p=pathlib.Path('config.json'); c=json.loads(p.read_text(encoding='utf-8')); c.setdefault('embed',{})['device']='cpu'; p.write_text(json.dumps(c,indent=2,ensure_ascii=False), encoding='utf-8'); print('[OK] config.json updated')"
if errorlevel 1 (
    echo [ERROR] Failed to update config.json.
    pause & exit /b 1
)

echo.
echo --- Import smoke test -------------------------------------------
%PY% -c "import torch; print('  torch', torch.__version__, '| CUDA available:', torch.cuda.is_available())"
if errorlevel 1 (
    echo [ERROR] torch still cannot be imported.
    pause & exit /b 1
)

%PY% -c "import sentence_transformers; print('  sentence-transformers OK')"
if errorlevel 1 (
    echo [ERROR] sentence-transformers still cannot be imported.
    pause & exit /b 1
)

echo.
echo ============================================================
echo   [DONE] CPU PyTorch runtime repaired.
echo   Run: python main.py
echo ============================================================
pause & exit /b 0
