@echo off
setlocal
cd /d "%~dp0"

echo =============================================
echo Fund Estimator Android Release One-Click Build
echo =============================================
echo.

powershell -ExecutionPolicy Bypass -File ".\scripts\one_click_release.ps1" -Artifact both
set EXIT_CODE=%ERRORLEVEL%

echo.
if %EXIT_CODE% NEQ 0 (
  echo Build failed. Exit code: %EXIT_CODE%
) else (
  echo Build succeeded.
)
echo.
pause
exit /b %EXIT_CODE%
