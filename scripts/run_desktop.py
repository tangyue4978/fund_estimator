from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

from storage import paths


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _bundle_root() -> Path:
    if _is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(str(meipass)).resolve()
    return Path(__file__).resolve().parents[1]


def _resolve_streamlit_app(root: Path) -> Path:
    candidates = (
        root / "app" / "Home.py",
        root / "_internal" / "app" / "Home.py",
    )
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError("Cannot find app/Home.py in bundled resources")


def _is_port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _acquire_lock(lock_path: Path) -> bool:
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("x", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    except FileExistsError:
        return False
    except Exception:
        return False


def _spawn_collector_dev(root: Path) -> None:
    cmd = [sys.executable, "-m", "scripts.intraday_collector", "--interval", "10", "--only-trading"]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(cmd, cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)


def _run_collector_inproc() -> None:
    try:
        from scripts.intraday_collector import main as collector_main

        collector_main(["--interval", "10", "--only-trading"])
    except Exception:
        pass


def _run_streamlit_inproc(root: Path, port: int) -> None:
    try:
        from streamlit.web import cli as stcli

        app_path = _resolve_streamlit_app(root)
        sys.argv = [
            "streamlit",
            "run",
            str(app_path),
            "--server.port",
            str(port),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
            "--server.fileWatcherType",
            "none",
        ]
        stcli.main()
    except Exception:
        traceback.print_exc()


def main() -> int:
    paths.ensure_dirs()

    bundle_root = _bundle_root()
    host, port = "127.0.0.1", 8501
    url = f"http://{host}:{port}"

    # Lock goes to writable runtime directory (e.g. %LOCALAPPDATA%\FundEstimator\status)
    lock_path = paths.status_dir() / "desktop.lock"
    if not _acquire_lock(lock_path):
        if _is_port_open(host, port):
            webbrowser.open(url)
        return 0

    if not _is_port_open(host, port):
        if _is_frozen():
            t1 = threading.Thread(target=_run_streamlit_inproc, args=(bundle_root, port), daemon=True)
            t1.start()

            t2 = threading.Thread(target=_run_collector_inproc, daemon=True)
            t2.start()
        else:
            _spawn_collector_dev(bundle_root)

            cmd = [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(bundle_root / "app" / "Home.py"),
                "--server.port",
                str(port),
                "--server.headless",
                "true",
                "--browser.gatherUsageStats",
                "false",
                "--server.fileWatcherType",
                "none",
            ]
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.Popen(cmd, cwd=str(bundle_root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)

        for _ in range(250):
            if _is_port_open(host, port):
                break
            time.sleep(0.1)

    if not _is_port_open(host, port):
        webbrowser.open(url)
        return 0

    import webview

    webview.create_window("Fund Estimator", url=url, width=1200, height=800, min_size=(1000, 700))
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
