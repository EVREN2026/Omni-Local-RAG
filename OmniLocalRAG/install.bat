@echo off
echo.
echo ============================================================
echo   Omni-Local RAG  --  Installation Launcher
echo ============================================================
echo.
echo   [1] CPU-only   -- No GPU / No CUDA drivers
echo   [2] CUDA GPU   -- NVIDIA GPU with CUDA drivers installed
echo.
set /p CHOICE="Select (1 or 2): "

if "%CHOICE%"=="1" (
    call install_cpu.bat
    exit /b %errorlevel%
)
if "%CHOICE%"=="2" (
    call install_cuda.bat
    exit /b %errorlevel%
)

echo [ERROR] Invalid choice. Enter 1 or 2.
pause & exit /b 1
