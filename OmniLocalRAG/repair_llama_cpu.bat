@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo   Omni-Local RAG  --  Repair llama-cpp-python Runtime
echo ============================================================
echo.

set "PY=python"
if exist "%CD%\venv\Scripts\python.exe" set "PY=%CD%\venv\Scripts\python.exe"
set "LLAMA_WHEEL_NAME=llama_cpp_python-0.3.19-cp312-cp312-win_amd64.whl"

"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Run install.bat first or activate Python 3.12 x64.
    pause & exit /b 1
)

"%PY%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
if errorlevel 1 (
    echo [ERROR] %LLAMA_WHEEL_NAME% requires Python 3.12 x64.
    pause & exit /b 1
)

call :find_llama_wheel
if not defined LLAMA_WHEEL (
    echo [ERROR] Missing %LLAMA_WHEEL_NAME%.
    echo         Put it in either the repository root or wheels\.
    pause & exit /b 1
)

echo [1/3] Removing existing llama-cpp-python ...
"%PY%" -m pip uninstall -y llama-cpp-python llama-cpp-python-cuBLAS llama-cpp-python-cuda >nul 2>&1

echo.
echo [2/3] Installing local llama wheel ...
"%PY%" -m pip install --no-cache-dir --force-reinstall "%LLAMA_WHEEL%"
if errorlevel 1 goto :error

echo.
echo [3/3] Setting config.json llm.n_gpu_layers=0 ...
"%PY%" -c "import json,pathlib; p=pathlib.Path('config.json'); c=json.loads(p.read_text(encoding='utf-8')); c.setdefault('llm',{})['n_gpu_layers']=0; p.write_text(json.dumps(c,indent=2,ensure_ascii=False), encoding='utf-8'); print('[OK] config.json updated')"
if errorlevel 1 goto :error

echo.
echo --- Import smoke test -------------------------------------------
"%PY%" -c "import llama_cpp; print('  llama-cpp-python OK')"
if errorlevel 1 goto :error

echo.
echo ============================================================
echo   [DONE] llama runtime repaired.
echo ============================================================
pause & exit /b 0

:find_llama_wheel
if exist "%CD%\wheels\%LLAMA_WHEEL_NAME%" set "LLAMA_WHEEL=%CD%\wheels\%LLAMA_WHEEL_NAME%"
if not defined LLAMA_WHEEL if exist "%CD%\%LLAMA_WHEEL_NAME%" set "LLAMA_WHEEL=%CD%\%LLAMA_WHEEL_NAME%"
exit /b 0

:error
echo.
echo [ERROR] llama-cpp-python repair failed.
pause & exit /b 1
