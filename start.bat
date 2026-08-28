@echo off
setlocal
cd /d "%~dp0"
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "POWERSHELL_EXE=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_EXE%" set "POWERSHELL_EXE=powershell.exe"

set "LAUNCH_SCRIPT=%~dp0scripts\launch.ps1"
if not exist "%LAUNCH_SCRIPT%" set "LAUNCH_SCRIPT=%~dp0installer\launch.ps1"
if not exist "%LAUNCH_SCRIPT%" (
    echo Subtitle Edit Bay launcher was not found.
    pause
    exit /b 1
)

"%POWERSHELL_EXE%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCH_SCRIPT%"
exit /b %ERRORLEVEL%
