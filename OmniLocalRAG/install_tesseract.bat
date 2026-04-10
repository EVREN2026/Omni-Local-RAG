@echo off
setlocal EnableExtensions EnableDelayedExpansion

echo.
echo ============================================================
echo   Omni-Local RAG  --  Tesseract Installer (Windows)
echo ============================================================
echo.

set "TESS_EXE="
set "TESS_DIR="
set "TESSDATA_DIR="

call :detect_tesseract
if defined TESS_EXE goto :configure

echo [INFO] Tesseract not found. Trying winget...
call :install_with_winget
call :detect_tesseract
if defined TESS_EXE goto :configure

echo [INFO] winget path failed. Trying chocolatey...
call :install_with_choco
call :detect_tesseract
if defined TESS_EXE goto :configure

echo [ERROR] Tesseract installation failed.
echo [HINT ] Install manually, then rerun this script:
echo        https://github.com/UB-Mannheim/tesseract/wiki
exit /b 1

:configure
for %%D in ("%TESS_DIR%") do set "TESS_DIR=%%~fD"
set "TESSDATA_DIR=%TESS_DIR%\tessdata"

echo [INFO] Tesseract found at: %TESS_EXE%
call :ensure_user_path "%TESS_DIR%"
set "PATH=%PATH%;%TESS_DIR%"

call :ensure_lang eng
call :ensure_lang chi_sim

echo.
echo [INFO] Verifying installation...
where tesseract >nul 2>nul
if errorlevel 1 (
  echo [WARN ] tesseract is installed but not visible in current shell PATH.
  echo        Open a new terminal and run: tesseract --version
) else (
  tesseract --version
  echo.
  tesseract --list-langs
)

echo.
echo [DONE ] Tesseract setup finished.
echo [NEXT ] Test OCR parser:
echo        cd scripts
echo        python test_parser_ocr.py C:\path\to\file.pdf
exit /b 0

:detect_tesseract
set "TESS_EXE="
set "TESS_DIR="
for /f "delims=" %%I in ('where tesseract 2^>nul') do (
  set "TESS_EXE=%%I"
  goto :detect_done
)
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" (
  set "TESS_EXE=%ProgramFiles%\Tesseract-OCR\tesseract.exe"
  goto :detect_done
)
if exist "%LocalAppData%\Programs\Tesseract-OCR\tesseract.exe" (
  set "TESS_EXE=%LocalAppData%\Programs\Tesseract-OCR\tesseract.exe"
  goto :detect_done
)
:detect_done
if defined TESS_EXE (
  for %%P in ("%TESS_EXE%") do set "TESS_DIR=%%~dpP"
  if defined TESS_DIR if "!TESS_DIR:~-1!"=="\" set "TESS_DIR=!TESS_DIR:~0,-1!"
)
exit /b 0

:install_with_winget
where winget >nul 2>nul
if errorlevel 1 (
  echo [WARN ] winget not found.
  exit /b 1
)

winget install --id UB-Mannheim.TesseractOCR -e --accept-source-agreements --accept-package-agreements --silent --disable-interactivity
if not errorlevel 1 exit /b 0

winget install --id tesseract-ocr.tesseract -e --accept-source-agreements --accept-package-agreements --silent --disable-interactivity
if not errorlevel 1 exit /b 0

echo [WARN ] winget install command failed.
exit /b 1

:install_with_choco
where choco >nul 2>nul
if errorlevel 1 (
  echo [WARN ] chocolatey not found.
  exit /b 1
)
choco install tesseract -y --no-progress
if errorlevel 1 (
  echo [WARN ] choco install command failed.
  exit /b 1
)
exit /b 0

:ensure_user_path
set "TARGET_DIR=%~1"
if "%TARGET_DIR%"=="" exit /b 0

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$target='%TARGET_DIR%';" ^
  "$current=[Environment]::GetEnvironmentVariable('Path','User');" ^
  "if([string]::IsNullOrWhiteSpace($current)){ $current='' };" ^
  "$parts=$current -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' };" ^
  "if(-not ($parts | Where-Object { $_.ToLowerInvariant() -eq $target.ToLowerInvariant() })){" ^
  "  $newPath=((@($parts)+@($target)) -join ';');" ^
  "  [Environment]::SetEnvironmentVariable('Path',$newPath,'User')" ^
  "}"
if errorlevel 1 (
  echo [WARN ] Could not persist PATH automatically. Add manually: %TARGET_DIR%
)
exit /b 0

:ensure_lang
set "LANG=%~1"
if "%LANG%"=="" exit /b 0
if not exist "%TESSDATA_DIR%" mkdir "%TESSDATA_DIR%" >nul 2>nul

if exist "%TESSDATA_DIR%\%LANG%.traineddata" (
  echo [INFO] Language already present: %LANG%
  exit /b 0
)

echo [INFO] Downloading language pack: %LANG%
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Invoke-WebRequest -Uri 'https://github.com/tesseract-ocr/tessdata_fast/raw/main/%LANG%.traineddata' -OutFile '%TESSDATA_DIR%\%LANG%.traineddata'"
if errorlevel 1 (
  echo [WARN ] Failed to download %LANG%.traineddata
  exit /b 1
)
exit /b 0
