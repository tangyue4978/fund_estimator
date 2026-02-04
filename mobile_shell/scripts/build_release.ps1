param(
    [ValidateSet("apk", "aab", "both")]
    [string]$Artifact = "both",
    [string]$StreamlitUrl = ""
)

$ErrorActionPreference = "Stop"

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Action,
        [Parameter(Mandatory = $true)]
        [string]$ErrorMessage
    )
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$ErrorMessage (exit code: $LASTEXITCODE)"
    }
}

function Resolve-AndroidSdkDir {
    param([string]$ProjectRoot)
    $candidates = @(
        $env:ANDROID_SDK_ROOT,
        $env:ANDROID_HOME,
        (Join-Path $env:LOCALAPPDATA "Android\Sdk")
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

    foreach ($dir in $candidates) {
        if (Test-Path $dir) {
            return (Resolve-Path $dir).Path
        }
    }
    return ""
}

function Ensure-AndroidLocalProperties {
    param(
        [string]$AndroidDir,
        [string]$ProjectRoot
    )
    $sdkDir = Resolve-AndroidSdkDir -ProjectRoot $ProjectRoot
    if ([string]::IsNullOrWhiteSpace($sdkDir)) {
        throw "Android SDK not found. Install Android SDK (Android Studio) and set ANDROID_HOME or ANDROID_SDK_ROOT."
    }
    $sdkForGradle = $sdkDir -replace "\\", "\\\\"
    $localPropsPath = Join-Path $AndroidDir "local.properties"
    Set-Content -Path $localPropsPath -Value "sdk.dir=$sdkForGradle" -Encoding ascii
    Write-Host "Using Android SDK: $sdkDir"
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$androidDir = Join-Path $projectRoot "android"
$keystorePropsPath = Join-Path $androidDir "keystore.properties"
$configureScript = Join-Path $PSScriptRoot "configure_release_signing.ps1"

Push-Location $projectRoot
try {
    if (-not (Test-Path (Join-Path $projectRoot "node_modules"))) {
        Write-Host "[1/5] Installing npm dependencies..."
        Invoke-Step -Action { npm install } -ErrorMessage "npm install failed"
    }

    if (-not (Test-Path $androidDir)) {
        Write-Host "[2/5] Creating Android project..."
        Invoke-Step -Action { npx --yes cap add android } -ErrorMessage "npx cap add android failed"
    }

    Ensure-AndroidLocalProperties -AndroidDir $androidDir -ProjectRoot $projectRoot

    Write-Host "[3/5] Verifying signing hook..."
    powershell -ExecutionPolicy Bypass -File $configureScript -EnsureOnly
    if ($LASTEXITCODE -ne 0) {
        throw "configure_release_signing.ps1 -EnsureOnly failed (exit code: $LASTEXITCODE)"
    }

    if (-not (Test-Path $keystorePropsPath)) {
        throw "Missing $keystorePropsPath. Run configure_release_signing.ps1 with your keystore settings first."
    }

    if (-not [string]::IsNullOrWhiteSpace($StreamlitUrl)) {
        $env:STREAMLIT_URL = $StreamlitUrl
    }
    Write-Host "[4/5] Syncing Capacitor assets..."
    Invoke-Step -Action { npx --yes cap sync android } -ErrorMessage "npx cap sync android failed"

    Push-Location $androidDir
    try {
        Write-Host "[5/5] Building release artifacts..."
        if ($Artifact -in @("apk", "both")) {
            Invoke-Step -Action { .\gradlew.bat assembleRelease } -ErrorMessage "Gradle assembleRelease failed"
        }
        if ($Artifact -in @("aab", "both")) {
            Invoke-Step -Action { .\gradlew.bat bundleRelease } -ErrorMessage "Gradle bundleRelease failed"
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}

$apkPath = Join-Path $androidDir "app\build\outputs\apk\release\app-release.apk"
$aabPath = Join-Path $androidDir "app\build\outputs\bundle\release\app-release.aab"

Write-Host "Build finished."
if (($Artifact -in @("apk", "both")) -and (Test-Path $apkPath)) {
    Write-Host "Signed APK: $apkPath"
}
if (($Artifact -in @("aab", "both")) -and (Test-Path $aabPath)) {
    Write-Host "Signed AAB: $aabPath"
}
