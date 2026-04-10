@echo off
setlocal

cd /d "%~dp0"

echo.
echo ============================================================
echo   Omni-Local RAG  --  System Function Tests
echo ============================================================
echo.

REM ---------- Directory guard -------------------------------------
if not exist main.py (
    echo [ERROR] Run this script from inside the OmniLocalRAG folder.
    echo         Expected main.py next to this batch file.
    pause & exit /b 1
)

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

REM ---------- Test path --------------------------------------------
set "PYTHONPATH=%CD%;%PYTHONPATH%"

echo.
echo [INFO] Running model loading, ingest/export, retrieval fallback, and SQLite metadata tests ...
echo.

%PY% -m unittest ^
    tests.test_embed_manager_device ^
    tests.test_inference_retrieval_fallback ^
    tests.test_llm_manager_load ^
    tests.test_pdf_vector_export ^
    tests.test_qa_memory ^
    tests.test_sqlite_metadata_store ^
    tests.test_video_asr_worker

if errorlevel 1 (
    echo.
    echo ============================================================
    echo   [FAILED] System function tests failed.
    echo ============================================================
    pause & exit /b 1
)

echo.
echo ============================================================
echo   [DONE] System function tests passed.
echo ============================================================
pause & exit /b 0
