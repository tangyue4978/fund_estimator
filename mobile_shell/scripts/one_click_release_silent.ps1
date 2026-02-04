param(
    [ValidateSet("apk", "aab", "both")]
    [string]$Artifact = "both"
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$configPath = Join-Path $projectRoot "release-config.local.json"
$configureScript = Join-Path $PSScriptRoot "configure_release_signing.ps1"
$buildScript = Join-Path $PSScriptRoot "build_release.ps1"
$androidDir = Join-Path $projectRoot "android"

if (-not (Test-Path $configPath)) {
    throw "Missing $configPath. Run BUILD_RELEASE_DOUBLE_CLICK.cmd once to initialize signing config."
}

$config = Get-Content -Path $configPath -Raw | ConvertFrom-Json

if ([string]::IsNullOrWhiteSpace([string]$config.keystorePath) -or
    [string]::IsNullOrWhiteSpace([string]$config.storePassword) -or
    [string]::IsNullOrWhiteSpace([string]$config.keyAlias) -or
    [string]::IsNullOrWhiteSpace([string]$config.keyPassword)) {
    throw "release-config.local.json is incomplete. Re-run BUILD_RELEASE_DOUBLE_CLICK.cmd and save complete config."
}

if (-not (Test-Path ([string]$config.keystorePath))) {
    throw "Keystore not found: $([string]$config.keystorePath)"
}

Push-Location $projectRoot
try {
    if (-not (Test-Path (Join-Path $projectRoot "node_modules"))) {
        Write-Host "Installing npm dependencies..."
        npm install
    }

    if (-not (Test-Path $androidDir)) {
        Write-Host "Creating Android project..."
        npx --yes cap add android
    }

    powershell -ExecutionPolicy Bypass -File $configureScript `
        -KeystorePath ([string]$config.keystorePath) `
        -StorePassword ([string]$config.storePassword) `
        -KeyAlias ([string]$config.keyAlias) `
        -KeyPassword ([string]$config.keyPassword)
    if ($LASTEXITCODE -ne 0) {
        throw "configure_release_signing.ps1 failed (exit code: $LASTEXITCODE)"
    }

    if ([string]::IsNullOrWhiteSpace([string]$config.streamlitUrl)) {
        powershell -ExecutionPolicy Bypass -File $buildScript -Artifact $Artifact
    }
    else {
        powershell -ExecutionPolicy Bypass -File $buildScript `
            -Artifact $Artifact `
            -StreamlitUrl ([string]$config.streamlitUrl)
    }
    if ($LASTEXITCODE -ne 0) {
        throw "build_release.ps1 failed (exit code: $LASTEXITCODE)"
    }
}
finally {
    Pop-Location
}
