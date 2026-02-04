param(
    [ValidateSet("apk", "aab", "both")]
    [string]$Artifact = "both"
)

$ErrorActionPreference = "Stop"

function Read-Value {
    param(
        [string]$Prompt,
        [string]$Default = "",
        [switch]$Secret
    )
    if ($Secret) {
        $secure = Read-Host -Prompt $Prompt -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            return [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
    }
    $value = Read-Host -Prompt $Prompt
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$configPath = Join-Path $projectRoot "release-config.local.json"
$configureScript = Join-Path $PSScriptRoot "configure_release_signing.ps1"
$buildScript = Join-Path $PSScriptRoot "build_release.ps1"
$androidDir = Join-Path $projectRoot "android"

$config = @{
    keystorePath = ""
    storePassword = ""
    keyAlias = ""
    keyPassword = ""
    streamlitUrl = ""
}

if (Test-Path $configPath) {
    try {
        $saved = Get-Content -Path $configPath -Raw | ConvertFrom-Json
        if ($saved.keystorePath) { $config.keystorePath = [string]$saved.keystorePath }
        if ($saved.storePassword) { $config.storePassword = [string]$saved.storePassword }
        if ($saved.keyAlias) { $config.keyAlias = [string]$saved.keyAlias }
        if ($saved.keyPassword) { $config.keyPassword = [string]$saved.keyPassword }
        if ($saved.streamlitUrl) { $config.streamlitUrl = [string]$saved.streamlitUrl }
    }
    catch {
        Write-Host "Config file is invalid: $configPath"
        throw
    }
}

Write-Host ""
Write-Host "Fund Estimator Android release build"
Write-Host "Project: $projectRoot"
Write-Host ""

$useSaved = "y"
if (Test-Path $configPath) {
    $useSaved = Read-Value -Prompt "Use saved signing config? (Y/n)" -Default "y"
}

if ($useSaved -notin @("y", "Y", "")) {
    $config.keystorePath = ""
    $config.storePassword = ""
    $config.keyAlias = ""
    $config.keyPassword = ""
}

if ([string]::IsNullOrWhiteSpace($config.keystorePath)) {
    $config.keystorePath = Read-Value -Prompt "Keystore path (example: D:\keys\release.jks)"
}
if ([string]::IsNullOrWhiteSpace($config.storePassword)) {
    $config.storePassword = Read-Value -Prompt "Store password" -Secret
}
if ([string]::IsNullOrWhiteSpace($config.keyAlias)) {
    $config.keyAlias = Read-Value -Prompt "Key alias"
}
if ([string]::IsNullOrWhiteSpace($config.keyPassword)) {
    $config.keyPassword = Read-Value -Prompt "Key password" -Secret
}

$streamlitPrompt = "Streamlit URL for app shell (blank to keep last/current)"
$inputStreamlit = Read-Value -Prompt $streamlitPrompt -Default $config.streamlitUrl
$config.streamlitUrl = $inputStreamlit

if ([string]::IsNullOrWhiteSpace($config.keystorePath) -or
    [string]::IsNullOrWhiteSpace($config.storePassword) -or
    [string]::IsNullOrWhiteSpace($config.keyAlias) -or
    [string]::IsNullOrWhiteSpace($config.keyPassword)) {
    throw "Signing fields are required."
}

if (-not (Test-Path $config.keystorePath)) {
    throw "Keystore not found: $($config.keystorePath)"
}

$config | ConvertTo-Json | Set-Content -Path $configPath -Encoding ascii

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
        -KeystorePath $config.keystorePath `
        -StorePassword $config.storePassword `
        -KeyAlias $config.keyAlias `
        -KeyPassword $config.keyPassword
    if ($LASTEXITCODE -ne 0) {
        throw "configure_release_signing.ps1 failed (exit code: $LASTEXITCODE)"
    }

    if ([string]::IsNullOrWhiteSpace($config.streamlitUrl)) {
        powershell -ExecutionPolicy Bypass -File $buildScript -Artifact $Artifact
    }
    else {
        powershell -ExecutionPolicy Bypass -File $buildScript -Artifact $Artifact -StreamlitUrl $config.streamlitUrl
    }
    if ($LASTEXITCODE -ne 0) {
        throw "build_release.ps1 failed (exit code: $LASTEXITCODE)"
    }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Done."
