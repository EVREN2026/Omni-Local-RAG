@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"

echo.
echo ============================================================
echo   Omni-Local RAG  --  Repair llama-cpp-python CPU Runtime
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

REM ---------- Remove broken native wheel ----------------------------
echo.
echo [1/3] Removing existing llama-cpp-python ...
%PY% -m pip uninstall -y llama-cpp-python llama-cpp-python-cuBLAS llama-cpp-python-cuda >nul 2>&1

REM ---------- Install CPU wheel -------------------------------------
echo.
echo [2/3] Installing CPU-only llama-cpp-python wheel ...
%PY% -m pip install --no-cache-dir --force-reinstall "llama-cpp-python==0.3.9" --prefer-binary ^
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to install CPU-only llama-cpp-python.
    echo         Check network access and Python version.
    pause & exit /b 1
)

REM ---------- Force CPU config --------------------------------------
echo.
echo [3/3] Setting config.json llm.n_gpu_layers=0 ...
%PY% -c "import json,pathlib; p=pathlib.Path('config.json'); c=json.loads(p.read_text(encoding='utf-8')); c.setdefault('llm',{})['n_gpu_layers']=0; p.write_text(json.dumps(c,indent=2,ensure_ascii=False), encoding='utf-8'); print('[OK] config.json updated')"
if errorlevel 1 (
    echo [ERROR] Failed to update config.json.
    pause & exit /b 1
)

echo.
echo --- Import smoke test -------------------------------------------
%PY% -c "import llama_cpp; print('  llama-cpp-python OK')"
if errorlevel 1 (
    echo [ERROR] llama-cpp-python still cannot be imported.
    pause & exit /b 1
)

echo.
echo ============================================================
echo   [DONE] CPU llama runtime repaired.
echo   Run: python main.py
echo ============================================================
pause & exit /b 0
