@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0build-windows-exe.ps1"
if %ERRORLEVEL% neq 0 (
    echo.
    echo Compilation echouee. Consulte les messages ci-dessus.
    pause
    exit /b %ERRORLEVEL%
)
pause
