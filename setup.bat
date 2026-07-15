@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
if errorlevel 1 (
    echo.
    echo Setup failed. Review the message above.
    pause
    exit /b 1
)
echo.
echo Setup completed. Double-click start.bat to launch Subtitle Edit Bay.
pause