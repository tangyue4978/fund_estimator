param(
    [string]$KeystorePath = "",
    [string]$StorePassword = "",
    [string]$KeyAlias = "",
    [string]$KeyPassword = "",
    [switch]$EnsureOnly
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$androidDir = Join-Path $projectRoot "android"
$appGradlePath = Join-Path $androidDir "app\build.gradle"
$signingGradlePath = Join-Path $androidDir "signing.gradle"
$keystorePropsPath = Join-Path $androidDir "keystore.properties"
$applyLine = "apply from: '../signing.gradle'"

if (-not (Test-Path $androidDir)) {
    throw "Missing Android project: $androidDir. Run 'npx cap add android' in mobile_shell first."
}

if (-not (Test-Path $appGradlePath)) {
    throw "Missing Gradle file: $appGradlePath"
}

$signingGradle = @'
import java.util.Properties

def keystorePropertiesFile = rootProject.file("keystore.properties")
def keystoreProperties = new Properties()
if (keystorePropertiesFile.exists()) {
    keystoreProperties.load(new FileInputStream(keystorePropertiesFile))
}

if (keystorePropertiesFile.exists()) {
    android {
        signingConfigs {
            release {
                storeFile file(keystoreProperties["storeFile"])
                storePassword keystoreProperties["storePassword"]
                keyAlias keystoreProperties["keyAlias"]
                keyPassword keystoreProperties["keyPassword"]
            }
        }
        buildTypes {
            release {
                signingConfig signingConfigs.release
            }
        }
    }
}
'@

Set-Content -Path $signingGradlePath -Value $signingGradle -Encoding ascii

$appGradle = Get-Content -Path $appGradlePath -Raw
if ($appGradle -notmatch [regex]::Escape($applyLine)) {
    $appGradle = $appGradle.TrimEnd() + "`r`n`r`n" + $applyLine + "`r`n"
    Set-Content -Path $appGradlePath -Value $appGradle -Encoding ascii
}

if ($EnsureOnly) {
    Write-Host "Signing hook is ready."
    exit 0
}

if (
    [string]::IsNullOrWhiteSpace($KeystorePath) -or
    [string]::IsNullOrWhiteSpace($StorePassword) -or
    [string]::IsNullOrWhiteSpace($KeyAlias) -or
    [string]::IsNullOrWhiteSpace($KeyPassword)
) {
    throw "KeystorePath / StorePassword / KeyAlias / KeyPassword are required unless -EnsureOnly is used."
}

if (-not (Test-Path $KeystorePath)) {
    throw "Keystore file not found: $KeystorePath"
}

$resolvedKeystorePath = (Resolve-Path $KeystorePath).Path
$gradleKeystorePath = $resolvedKeystorePath -replace "\\", "\\\\"

$props = @(
    "storeFile=$gradleKeystorePath"
    "storePassword=$StorePassword"
    "keyAlias=$KeyAlias"
    "keyPassword=$KeyPassword"
)

Set-Content -Path $keystorePropsPath -Value $props -Encoding ascii
Write-Host "Release signing configured:"
Write-Host "  - $signingGradlePath"
Write-Host "  - $keystorePropsPath"
