@echo off
setlocal
cd /d "%~dp0"

echo =============================================
echo Fund Estimator Android Silent Release Build
echo =============================================
echo.

powershell -ExecutionPolicy Bypass -File ".\scripts\one_click_release_silent.ps1" -Artifact both
set EXIT_CODE=%ERRORLEVEL%

echo.
if %EXIT_CODE% NEQ 0 (
  echo Build failed. Exit code: %EXIT_CODE%
  echo Tip: run BUILD_RELEASE_DOUBLE_CLICK.cmd once if config is missing.
) else (
  echo Build succeeded.
)
echo.
pause
exit /b %EXIT_CODE%
