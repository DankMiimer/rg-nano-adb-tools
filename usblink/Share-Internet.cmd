@echo off
setlocal
set "PAUSE_FLAG="
if "%~1"=="" (
    echo %cmdcmdline% | find /i "%~f0" >nul && set "PAUSE_FLAG=-PauseOnExit"
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0share-internet.ps1" %PAUSE_FLAG% %*
exit /b %ERRORLEVEL%
