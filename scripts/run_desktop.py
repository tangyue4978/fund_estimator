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


def _read_lock_pid(lock_path: Path) -> int | None:
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
        pid = int(raw) if raw else 0
        return pid if pid > 0 else None
    except Exception:
        return None


def _is_pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            out = (proc.stdout or "").strip()
            # not found sample: INFO: No tasks are running which match the specified criteria.
            if (not out) or out.upper().startswith("INFO:"):
                return False
            return f'"{int(pid)}"' in out
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _pick_available_port(host: str, preferred_port: int) -> int:
    if preferred_port > 0 and (not _is_port_open(host, preferred_port)):
        return preferred_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _acquire_lock_with_recovery(lock_path: Path) -> bool:
    if _acquire_lock(lock_path):
        return True
    # Lock exists: recover stale lock from dead process.
    pid = _read_lock_pid(lock_path)
    if pid and (not _is_pid_alive(pid)):
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            return False
        return _acquire_lock(lock_path)
    return False


def _spawn_collector_dev(root: Path) -> None:
    cmd = [sys.executable, "-m", "scripts.intraday_collector", "--interval", "30", "--only-trading"]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(cmd, cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)


def _run_collector_inproc() -> None:
    try:
        from scripts.intraday_collector import main as collector_main

        collector_main(["--interval", "30", "--only-trading"])
    except Exception:
        pass


def _run_streamlit_inproc(root: Path, port: int) -> None:
    try:
        import signal
        from streamlit.web import cli as stcli

        app_path = _resolve_streamlit_app(root)
        # Force packaged app to run in production mode even if user-level
        # Streamlit config has global.developmentMode=true.
        os.environ["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"
        original_signal = signal.signal

        def _safe_signal(sig: int, handler):  # type: ignore[no-untyped-def]
            try:
                return original_signal(sig, handler)
            except ValueError:
                # Streamlit installs signal handlers; in frozen mode we run it in
                # a worker thread where signal.signal is not allowed.
                return None

        signal.signal = _safe_signal  # type: ignore[assignment]
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
            "--global.developmentMode",
            "false",
        ]
        try:
            stcli.main()
        finally:
            signal.signal = original_signal  # type: ignore[assignment]
    except Exception:
        _write_startup_log("streamlit inproc failed", traceback.format_exc())


def _startup_log_path() -> Path:
    try:
        if hasattr(paths, "logs_dir"):
            d = Path(paths.logs_dir())
        elif hasattr(paths, "runtime_root"):
            d = Path(paths.runtime_root()) / "logs"
            d.mkdir(parents=True, exist_ok=True)
        else:
            d = Path.home() / ".fund_estimator" / "logs"
            d.mkdir(parents=True, exist_ok=True)
        return d / "desktop_startup.log"
    except Exception:
        return Path.cwd() / "desktop_startup.log"


def _write_startup_log(msg: str, detail: str = "") -> None:
    try:
        p = _startup_log_path()
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}\n"
        if detail:
            line += f"{detail}\n"
        with p.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _show_error_dialog(title: str, message: str) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except Exception:
        pass


def _read_last_port(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        port = int(raw) if raw else 0
        return port if port > 0 else None
    except Exception:
        return None


def _write_last_port(path: Path, port: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(int(port)), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    paths.ensure_dirs()

    bundle_root = _bundle_root()
    host = "127.0.0.1"

    # Lock goes to writable runtime directory (e.g. %LOCALAPPDATA%\FundEstimator\status)
    if hasattr(paths, "status_dir"):
        lock_base = Path(paths.status_dir())
    elif hasattr(paths, "runtime_root"):
        lock_base = Path(paths.runtime_root()) / "status"
        lock_base.mkdir(parents=True, exist_ok=True)
    else:
        lock_base = Path.home() / ".fund_estimator" / "status"
        lock_base.mkdir(parents=True, exist_ok=True)
    lock_path = lock_base / "desktop.lock"
    port_path = lock_base / "desktop_port.txt"
    last_port = _read_last_port(port_path)
    if not _acquire_lock_with_recovery(lock_path):
        probe_ports = [p for p in (last_port, 8501) if p]
        for p in probe_ports:
            if _is_port_open(host, p):
                webbrowser.open(f"http://{host}:{p}")
                break
        return 0

    preferred_port = last_port or 8501
    port = _pick_available_port(host, preferred_port)
    _write_last_port(port_path, port)
    url = f"http://{host}:{port}"

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
            "--global.developmentMode",
            "false",
        ]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        env = os.environ.copy()
        env["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"
        subprocess.Popen(
            cmd,
            cwd=str(bundle_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            env=env,
        )

    for _ in range(250):
        if _is_port_open(host, port):
            break
        time.sleep(0.1)

    if not _is_port_open(host, port):
        _write_startup_log(
            "streamlit startup timeout",
            f"host={host} port={port} bundle_root={bundle_root}",
        )
        _show_error_dialog(
            "Fund Estimator",
            (
                "Desktop app failed to start.\n\n"
                "Please open startup log:\n"
                f"{_startup_log_path()}"
            ),
        )
        return 1

    try:
        import webview
        webview.create_window("Fund Estimator", url=url, width=1200, height=800, min_size=(1000, 700))
        webview.start()
        return 0
    except Exception:
        _write_startup_log("webview start failed; fallback to browser", traceback.format_exc())
        webbrowser.open(url)
        return 0
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        _write_startup_log("fatal startup error", traceback.format_exc())
        raise
