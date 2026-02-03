# Fund Estimator Packaging Guide

This project now supports packaging to a distributable Windows desktop executable.

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
- Build helper script: `scripts/build_exe.py`
- If build fails with missing packages, check internal network/proxy settings first.
