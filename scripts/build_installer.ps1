param(
    [switch]$BuildExe
)

$ErrorActionPreference = 'Stop'

if ($BuildExe) {
    .\.venv\Scripts\python.exe scripts\build_exe.py --clean
}

$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
    Write-Error 'Inno Setup compiler (iscc) not found in PATH. Install Inno Setup first.'
}

& $iscc.Source scripts\installer.iss
Write-Host 'Installer created under dist\FundEstimator-Setup.exe'
