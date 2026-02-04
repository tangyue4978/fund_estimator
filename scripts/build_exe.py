from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _data_sep() -> str:
    return ";" if os.name == "nt" else ":"


def _add_data_arg(src: Path, dst: str) -> str:
    return f"{src}{_data_sep()}{dst}"


def _check_required_modules() -> None:
    missing: list[str] = []
    for module in ("PyInstaller", "webview"):
        try:
            __import__(module)
        except Exception:
            missing.append(module)
    if missing:
        raise RuntimeError(
            "Missing required packages for build: "
            + ", ".join(missing)
            + ". Install with: "
            + f"{sys.executable} -m pip install pyinstaller pywebview"
        )


def build(*, onefile: bool, clean: bool) -> None:
    _check_required_modules()

    entry = PROJECT_ROOT / "scripts" / "run_desktop.py"
    dist_name = "FundEstimator"
    work_path = Path(tempfile.mkdtemp(prefix="fund_estimator_pyi_"))

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--name",
        dist_name,
        "--windowed",
        "--log-level",
        "WARN",
        "--distpath",
        str(PROJECT_ROOT / "dist"),
        "--workpath",
        str(work_path),
        "--specpath",
        str(PROJECT_ROOT),
        "--paths",
        str(PROJECT_ROOT),
    ]

    cmd.append("--onefile" if onefile else "--onedir")
    if clean:
        cmd.append("--clean")

    data_dirs = (
        (PROJECT_ROOT / "app", "app"),
        (PROJECT_ROOT / "config", "config"),
        (PROJECT_ROOT / "data", "data"),
        (PROJECT_ROOT / "services", "services"),
        (PROJECT_ROOT / "storage", "storage"),
    )
    for src, dst in data_dirs:
        cmd += ["--add-data", _add_data_arg(src, dst)]

    collect_all = (
        "streamlit",
        "pydeck",
        "altair",
        "plotly",
        "pandas",
        "numpy",
        "pyarrow",
    )
    for pkg in collect_all:
        cmd += ["--collect-all", pkg]

    hidden_imports = (
        "streamlit.web.cli",
        # Streamlit pages import service modules dynamically at runtime.
        "services.portfolio_service",
        "services.watchlist_service",
        "services.estimation_service",
        "services.intraday_service",
        "services.settlement_service",
        "services.snapshot_service",
        "services.history_service",
        "services.adjustment_service",
        "services.accuracy_service",
        "services.edit_bridge_service",
        "services.fund_service",
    )
    for mod in hidden_imports:
        cmd += ["--hidden-import", mod]

    for pkg in ("services", "storage"):
        cmd += ["--collect-submodules", pkg]

    cmd.append(str(entry))

    print("[build] Running:", " ".join(cmd))
    subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True)

    if onefile:
        out = PROJECT_ROOT / "dist" / f"{dist_name}.exe"
    else:
        out = PROJECT_ROOT / "dist" / dist_name / f"{dist_name}.exe"
    print(f"[build] Done: {out}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build FundEstimator executable with PyInstaller")
    parser.add_argument("--onefile", action="store_true", help="Build single-file exe (slower startup, easier to share)")
    parser.add_argument("--clean", action="store_true", help="Clean PyInstaller cache before build")
    args = parser.parse_args()

    build(onefile=bool(args.onefile), clean=bool(args.clean))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
