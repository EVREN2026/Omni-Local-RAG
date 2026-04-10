@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo   Omni-Local RAG  --  CUDA Installation
echo   Uses CUDA PyTorch for embeddings and local llama wheel
echo ============================================================
echo.

set "VENV_DIR=%CD%\venv"
set "PY=%VENV_DIR%\Scripts\python.exe"
set "MINERU_VENV=%CD%\tools\mineru-venv"
set "MINERU_PY=%MINERU_VENV%\Scripts\python.exe"
set "LLAMA_WHEEL_NAME=llama_cpp_python-0.3.19-cp312-cp312-win_amd64.whl"

if not exist "%PY%" (
    echo [1/9] Creating local Python 3.12 venv ...
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
    echo [1/9] Using existing local venv: %VENV_DIR%
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
echo [2/9] Checking NVIDIA driver ...
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo [ERROR] nvidia-smi not found. Use install_cpu.bat or install NVIDIA drivers first.
    pause & exit /b 1
)
for /f "delims=" %%c in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$line=(nvidia-smi | Select-String -Pattern 'CUDA Version' | Select-Object -First 1).Line; if ($line -match 'CUDA Version:\s*([0-9]+(?:\.[0-9]+)?)') { $matches[1] }"') do set "CUDA_VER_FULL=%%c"
if "%CUDA_VER_FULL%"=="" (
    echo [ERROR] Could not detect CUDA driver version from nvidia-smi.
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
        echo [ERROR] Detected CUDA %CUDA_VER_FULL%. PyTorch >=2.7 CUDA wheels require driver support for CUDA 12.6+.
        echo         Use install_cpu.bat or update NVIDIA driver.
        pause & exit /b 1
    )
) else (
    echo [ERROR] Detected CUDA %CUDA_VER_FULL%. Use install_cpu.bat or update NVIDIA driver.
    pause & exit /b 1
)
echo [OK] Detected CUDA %CUDA_VER_FULL%; using PyTorch wheel tag %CUDA_TAG%

echo.
echo [3/9] Upgrading pip/setuptools/wheel ...
"%PY%" -m pip install --upgrade pip setuptools wheel --quiet
if errorlevel 1 goto :error

echo.
echo [4/9] Installing PyTorch %CUDA_TAG% build ...
"%PY%" -m pip install --upgrade --force-reinstall torch torchvision torchaudio ^
    --index-url https://download.pytorch.org/whl/%CUDA_TAG%
if errorlevel 1 goto :error

echo.
echo [5/9] Installing local llama-cpp-python wheel ...
echo       Note: the provided win_amd64 wheel is treated as CPU llama runtime.
"%PY%" -m pip uninstall -y llama-cpp-python llama-cpp-python-cuBLAS llama-cpp-python-cuda >nul 2>&1
"%PY%" -m pip install --no-cache-dir --force-reinstall "%LLAMA_WHEEL%"
if errorlevel 1 goto :llama_error

echo.
echo [6/9] Installing remaining Python dependencies ...
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo [7/9] Installing MinerU in isolated venv ...
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
echo [8/9] Writing CUDA config ...
"%PY%" -c "import json,pathlib; p=pathlib.Path('config.json'); c=json.loads(p.read_text(encoding='utf-8')); c.setdefault('llm',{})['n_gpu_layers']=0; c.setdefault('embed',{})['device']='cuda'; mineru=str((pathlib.Path('tools')/'mineru-venv'/'Scripts'/'mineru.exe').resolve()); c.setdefault('pdf',{})['mineru_cmd']=mineru; c['pdf'].setdefault('parser_options',{}).setdefault('mineru',{})['command']=mineru; p.write_text(json.dumps(c,indent=2,ensure_ascii=False), encoding='utf-8'); print('[OK] config.json updated: embed=cuda, llama=cpu wheel')"
if errorlevel 1 goto :error

echo.
echo [9/9] Import smoke test ...
"%PY%" -c "import torch; print('  torch', torch.__version__, '| CUDA available:', torch.cuda.is_available()); print('  GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"
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
echo   [DONE] CUDA installation complete.
echo   Run: "%PY%" main.py
echo   Embedding uses CUDA; llama uses the provided local CPU wheel.
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
