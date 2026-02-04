# Android APK (WebView shell)

This folder contains a Capacitor-based Android shell that loads your running Streamlit service.

## 1) Start Streamlit for LAN access

From project root:

```powershell
.\.venv\Scripts\python.exe scripts\run_mobile_server.py
```

Keep it running. Your phone and PC must be on the same LAN.

## 2) Configure Streamlit URL

Use your PC LAN IP (example `192.168.1.100`):

```powershell
cd mobile_shell
$env:STREAMLIT_URL="http://192.168.1.100:8501"
```

## 3) Install dependencies and sync Android project

```powershell
npm install
npx cap add android
npm run sync
```

## 4) Build APK

Debug APK:

```powershell
npm run build:debug
```

Output:
- `mobile_shell\android\app\build\outputs\apk\debug\app-debug.apk`

Release APK:

```powershell
npm run build:release
```

Then sign it with your keystore before distribution.

## 5) Release signing (once)

1) Ensure Android project exists:

```powershell
npx cap add android
```

2) Configure signing with your keystore:

```powershell
npm run signing:setup -- `
  -KeystorePath "D:\keys\fund-estimator-release.jks" `
  -StorePassword "your_store_password" `
  -KeyAlias "fund-estimator" `
  -KeyPassword "your_key_password"
```

It will:
- create/update `mobile_shell\android\signing.gradle`
- create `mobile_shell\android\keystore.properties` (git ignored)
- wire `app/build.gradle` to apply signing settings

## 6) One-click formal package

Build signed release APK + AAB:

```powershell
npm run build:release:signed
```

Double-click mode (recommended for daily use):

- Double-click `mobile_shell\BUILD_RELEASE_DOUBLE_CLICK.cmd`
- On first run it asks for keystore info and saves local config to `mobile_shell\release-config.local.json` (git ignored)
- Later runs can directly reuse saved config

No-prompt mode (fully non-interactive):

- Double-click `mobile_shell\BUILD_RELEASE_NO_PROMPT.cmd`
- Requires existing `mobile_shell\release-config.local.json`
- If config/keystore is missing, it fails immediately with guidance

Optional: set backend URL during sync:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 `
  -Artifact both `
  -StreamlitUrl "http://192.168.1.100:8501"
```

Output:
- `mobile_shell\android\app\build\outputs\apk\release\app-release.apk`
- `mobile_shell\android\app\build\outputs\bundle\release\app-release.aab`

## Notes

- If using HTTP URL, cleartext is enabled automatically.
- For production, use an HTTPS endpoint and fixed backend domain.
- This shell does not package Python runtime into the APK; it connects to your running service.
