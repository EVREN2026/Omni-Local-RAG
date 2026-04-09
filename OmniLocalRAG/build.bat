@echo off
setlocal
echo.
echo ============================================================
echo   Omni-Local RAG  --  PyInstaller Build
echo ============================================================
echo.

REM ---------- Directory guard -------------------------------------
if not exist main.py (
    echo [ERROR] Run this script from inside the OmniLocalRAG\ folder.
    echo         Example:  cd OmniLocalRAG  ^&^&  build.bat
    pause & exit /b 1
)

REM ---------- Python check -----------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Run install_cpu.bat or install_cuda.bat first.
    pause & exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [OK] Python %PY_VER%

REM ---------- PyInstaller check / install --------------------------
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [INFO] PyInstaller not found, installing ...
    pip install "pyinstaller>=6.0" --quiet
    if errorlevel 1 (
        echo [ERROR] Failed to install PyInstaller.
        pause & exit /b 1
    )
    echo [OK] PyInstaller installed
) else (
    for /f "tokens=2" %%v in ('pip show pyinstaller ^| findstr /i "Version"') do set PI_VER=%%v
    echo [OK] PyInstaller %PI_VER%
)

REM ---------- Clean previous build ---------------------------------
if exist dist\OmniLocal (
    echo [INFO] Removing previous dist\OmniLocal ...
    rmdir /s /q dist\OmniLocal
)
if exist build\OmniLocal (
    echo [INFO] Removing previous build\OmniLocal ...
    rmdir /s /q build\OmniLocal
)

REM ---------- Build ------------------------------------------------
echo.
echo [INFO] Running PyInstaller ...
echo.
pyinstaller OmniLocal.spec --noconfirm
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Check the output above.
    pause & exit /b 1
)

REM ---------- Copy models notice -----------------------------------
echo.
echo ============================================================
echo   [DONE] Build complete.
echo.
echo   Output: dist\OmniLocal\OmniLocal.exe
echo.
echo   IMPORTANT: Copy the models\ folder into dist\OmniLocal\
echo   before distributing or running the packaged app:
echo.
echo     xcopy /E /I models dist\OmniLocal\models
echo.
echo   The data\ folder (ChromaDB + SQLite) is created
echo   automatically on first run -- do NOT copy it.
echo ============================================================
pause & exit /b 0
