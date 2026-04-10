@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo   Omni-Local RAG  --  CPU-only Installation
echo   Creates/uses local venv and installs Python dependencies
echo ============================================================
echo.

set "VENV_DIR=%CD%\venv"
set "PY=%VENV_DIR%\Scripts\python.exe"
set "MINERU_VENV=%CD%\tools\mineru-venv"
set "MINERU_PY=%MINERU_VENV%\Scripts\python.exe"
set "LLAMA_WHEEL_NAME=llama_cpp_python-0.3.19-cp312-cp312-win_amd64.whl"

if not exist "%PY%" (
    echo [1/8] Creating local Python 3.12 venv ...
    py -3.12 -m venv "%VENV_DIR%" >nul 2>&1
    if errorlevel 1 (
        python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
        if errorlevel 1 (
            echo [ERROR] Python 3.12 x64 is required for %LLAMA_WHEEL_NAME%.
            echo         Install Python 3.12 x64, then run install.bat again.
            pause & exit /b 1
        )
        python -m venv "%VENV_DIR%"
        if errorlevel 1 goto :error
    )
) else (
    echo [1/8] Using existing local venv: %VENV_DIR%
)

for /f "tokens=2" %%v in ('"%PY%" --version 2^>^&1') do set PY_VER=%%v
echo [OK] Python %PY_VER% ^(via %PY%^)
"%PY%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
if errorlevel 1 (
    echo [ERROR] Existing venv is not Python 3.12. Delete venv and rerun install.bat.
    pause & exit /b 1
)

call :find_llama_wheel
if not defined LLAMA_WHEEL (
    echo [ERROR] Missing %LLAMA_WHEEL_NAME%.
    echo         Put it in either:
    echo           %CD%\%LLAMA_WHEEL_NAME%
    echo           %CD%\wheels\%LLAMA_WHEEL_NAME%
    pause & exit /b 1
)
echo [OK] llama wheel: %LLAMA_WHEEL%

echo.
echo [2/8] Upgrading pip/setuptools/wheel ...
"%PY%" -m pip install --upgrade pip setuptools wheel --quiet
if errorlevel 1 goto :error

echo.
echo [3/8] Installing PyTorch CPU build ...
"%PY%" -m pip install --upgrade --force-reinstall torch torchvision torchaudio ^
    --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto :error

echo.
echo [4/8] Installing local llama-cpp-python wheel ...
"%PY%" -m pip uninstall -y llama-cpp-python llama-cpp-python-cuBLAS llama-cpp-python-cuda >nul 2>&1
"%PY%" -m pip install --no-cache-dir --force-reinstall "%LLAMA_WHEEL%"
if errorlevel 1 goto :llama_error

echo.
echo [5/8] Installing remaining Python dependencies ...
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo [6/8] Installing MinerU in isolated venv ...
if not exist "%MINERU_PY%" (
    py -3.12 -m venv "%MINERU_VENV%" >nul 2>&1
    if errorlevel 1 "%PY%" -m venv "%MINERU_VENV%"
    if errorlevel 1 goto :error
)
"%MINERU_PY%" -m pip install --upgrade pip setuptools wheel --quiet
if errorlevel 1 goto :error
"%MINERU_PY%" -m pip install --upgrade --force-reinstall "mineru==2.0.6"
if errorlevel 1 goto :mineru_error
if not exist "%MINERU_VENV%\Scripts\mineru.exe" goto :mineru_error

echo.
echo [7/8] Writing CPU config ...
"%PY%" -c "import json,pathlib; p=pathlib.Path('config.json'); c=json.loads(p.read_text(encoding='utf-8')); c.setdefault('llm',{})['n_gpu_layers']=0; c.setdefault('embed',{})['device']='cpu'; mineru=str((pathlib.Path('tools')/'mineru-venv'/'Scripts'/'mineru.exe').resolve()); c.setdefault('pdf',{})['mineru_cmd']=mineru; c['pdf'].setdefault('parser_options',{}).setdefault('mineru',{})['command']=mineru; p.write_text(json.dumps(c,indent=2,ensure_ascii=False), encoding='utf-8'); print('[OK] config.json updated')"
if errorlevel 1 goto :error

echo.
echo [8/8] Import smoke test ...
"%PY%" -c "import torch; print('  torch', torch.__version__, '| CUDA available:', torch.cuda.is_available())"
if errorlevel 1 goto :error
"%PY%" -c "import llama_cpp; print('  llama-cpp-python OK')"
if errorlevel 1 goto :error
"%PY%" -c "import sentence_transformers; print('  sentence-transformers OK')"
if errorlevel 1 goto :error
"%PY%" -c "import PyQt5; print('  PyQt5 OK')"
if errorlevel 1 goto :error
"%PY%" -c "from app.parsers.document_parsers import available_parser_names; print('  parsers', ','.join(available_parser_names()))"
if errorlevel 1 goto :error
"%MINERU_VENV%\Scripts\mineru.exe" --help >nul 2>&1
if errorlevel 1 goto :mineru_error
where tesseract >nul 2>&1
if errorlevel 1 (
    echo   [WARN] tesseract.exe not found. OCR parser needs external Tesseract.
    echo          Run install_tesseract.bat if OCR import is required.
) else (
    echo   Tesseract OK
)
"%MINERU_PY%" -m pip check
if errorlevel 1 goto :pip_check_error
"%PY%" -m pip check
if errorlevel 1 goto :pip_check_error

echo.
echo ============================================================
echo   [DONE] CPU installation complete.
echo   Run: "%PY%" main.py
echo ============================================================
pause & exit /b 0

:find_llama_wheel
if exist "%CD%\wheels\%LLAMA_WHEEL_NAME%" set "LLAMA_WHEEL=%CD%\wheels\%LLAMA_WHEEL_NAME%"
if not defined LLAMA_WHEEL if exist "%CD%\%LLAMA_WHEEL_NAME%" set "LLAMA_WHEEL=%CD%\%LLAMA_WHEEL_NAME%"
exit /b 0

:llama_error
echo.
echo [ERROR] Failed to install local llama-cpp-python wheel.
echo         Confirm the wheel is cp312 win_amd64 and this venv is Python 3.12 x64.
pause & exit /b 1

:mineru_error
echo.
echo [ERROR] MinerU isolated install failed.
echo         Main dependencies may still be usable, but the MinerU parser will not work.
pause & exit /b 1

:pip_check_error
echo.
echo [ERROR] pip check found dependency conflicts.
echo         The install completed partially, but the environment is not clean.
pause & exit /b 1

:error
echo.
echo [ERROR] Installation failed. Check the output above.
pause & exit /b 1
