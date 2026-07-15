@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update.ps1"
if errorlevel 1 (
    echo.
    echo Update failed. Review the message above.
    pause
    exit /b 1
)
echo.
echo Update completed. Double-click start.bat to launch Subtitle Edit Bay.
pause
