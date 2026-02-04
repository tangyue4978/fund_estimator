# Fund Estimator Packaging Guide

This project now supports packaging to a distributable Windows desktop executable.
It also includes an Android WebView shell scaffold under `mobile_shell/`.
It can also be deployed as a web app on Streamlit Community Cloud.

## 1) Prepare environment

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

## 2) Build executable

Recommended (folder mode, easiest for installer packaging):

```powershell
.\.venv\Scripts\python.exe scripts\build_exe.py --clean
```

Output:
- `dist\FundEstimator\FundEstimator.exe`

Single-file mode (portable sharing):

```powershell
.\.venv\Scripts\python.exe scripts\build_exe.py --onefile --clean
```

Output:
- `dist\FundEstimator.exe`

## 3) Data location after packaging

For packaged builds, writable runtime data is moved to:
- `%LOCALAPPDATA%\FundEstimator\data`

You can override this path by setting:
- `FUND_ESTIMATOR_HOME`

This avoids write-permission issues under `Program Files` and makes installer deployment stable.

## 4) Share/install


Optional: build a Windows installer (requires Inno Setup):

```powershell
.\scripts\build_installer.ps1
```

Output:
- `dist\FundEstimator-Setup.exe`
- If you built `--onefile`: share `dist\FundEstimator.exe` directly.
- If you built default `--onedir`: zip the full `dist\FundEstimator` folder and share the zip.

## Notes

- Entry point for desktop app: `scripts/run_desktop.py`
- Entry point for Android LAN backend mode: `scripts/run_mobile_server.py`
- Entry point for web deployment: `app/Home.py`
- Build helper script: `scripts/build_exe.py`
- If build fails with missing packages, check internal network/proxy settings first.
- Android shell instructions: `mobile_shell/README.md`

## Web Deploy (Streamlit Community Cloud)

1) Push this repo to GitHub.

2) Go to Streamlit Community Cloud and create a new app:
- Repository: your repo
- Branch: your branch
- Main file path: `app/Home.py`

3) Deploy and open the generated URL.

Notes:
- `pywebview` is installed only on Windows; cloud deployment skips it automatically.
- Streamlit Cloud filesystem is ephemeral. Runtime data is redirected to `~/.fund_estimator/data` in cloud runs.
- Multi-user split is file-based by `User ID`, with per-user paths under `~/.fund_estimator/data/users/<user_id>`.
- Auth is phone+password (no SMS verification), password min length is 6.
- After login, user data is isolated under `~/.fund_estimator/data/users/u_<phone>/`.
