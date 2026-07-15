@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

if exist "%~dp0.local\ffmpeg_path.txt" (
    set /p "FFMPEG_DIR="<"%~dp0.local\ffmpeg_path.txt"
    if defined FFMPEG_DIR set "PATH=%FFMPEG_DIR%;%PATH%"
)

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Subtitle Edit Bay is not set up yet.
    echo Run setup.bat first.
    pause
    exit /b 1
)

"%~dp0.venv\Scripts\python.exe" -m src.gui
if errorlevel 1 (
    echo.
    echo Subtitle Edit Bay exited with an error.
    pause
    exit /b 1
)