@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo   Omni-Local RAG  --  Repair llama-server Runtime
echo   (legacy script name kept for compatibility)
echo ============================================================
echo.

set "PY=python"
if exist "%CD%\venv\Scripts\python.exe" set "PY=%CD%\venv\Scripts\python.exe"

"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Run install.bat first.
    pause & exit /b 1
)

echo [1/3] Stopping stale llama-server processes ...
taskkill /F /IM llama-server.exe >nul 2>&1

echo.
echo [2/3] Validating llama-server binary and model path ...
"%PY%" -c "from app.models.llama_server_manager import _find_bundled_server; from app.utils import config as cfg; exe=_find_bundled_server(); model=cfg.abs_path(cfg.get('llama_server.model_path', cfg.get('llm.model_path',''))); assert exe, 'llama-server.exe not found'; assert model.exists(), f'GGUF model not found: {model}'; print('  llama-server:', exe); print('  model:', model)"
if errorlevel 1 goto :error

echo.
echo [3/3] Starting health probe ...
"%PY%" -c "from app.models.llama_server_manager import LlamaServerManager; m=LlamaServerManager(); ok=m.ensure_started(); print('  ensure_started=', ok); print('  base_url=', m.base_url); print('  last_error=', m.last_error); raise SystemExit(0 if ok else 1)"
if errorlevel 1 goto :error

echo.
echo ============================================================
echo   [DONE] llama-server runtime check passed.
echo ============================================================
pause & exit /b 0

:error
echo.
echo [ERROR] llama-server runtime check failed.
pause & exit /b 1