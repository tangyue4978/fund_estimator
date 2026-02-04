param(
    [switch]$BuildExe
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Resolve-IsccPath {
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "D:\Inno Setup 6\ISCC.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

Push-Location $projectRoot
try {
    if ($BuildExe) {
        .\.venv\Scripts\python.exe scripts\build_exe.py --clean
        if ($LASTEXITCODE -ne 0) {
            throw "EXE build failed with exit code $LASTEXITCODE. Stop installer packaging to avoid stale artifacts."
        }
    }

    $isccPath = Resolve-IsccPath
    if (-not $isccPath) {
        Write-Error 'Inno Setup compiler (iscc) not found. Install Inno Setup or add ISCC.exe to PATH.'
    }

    & $isccPath scripts\installer.iss
    if ($LASTEXITCODE -eq 0) {
        Write-Host 'Installer created under dist\FundEstimator-Setup.exe'
    } else {
        Write-Error "Installer build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
