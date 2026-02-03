@echo off
setlocal
cd /d "%~dp0"

echo [FundEstimator] Building EXE + installer...
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build_installer.ps1" -BuildExe

if errorlevel 1 (
  echo.
  echo Build failed.
) else (
  echo.
  echo Build succeeded: dist\FundEstimator-Setup.exe
)

echo.
pause
