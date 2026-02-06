from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from config import settings
from storage import paths


def _collector_pid_path() -> Path:
    if hasattr(paths, "status_dir"):
        d = paths.status_dir()
    elif hasattr(paths, "runtime_root"):
        d = Path(paths.runtime_root()) / "status"
        d.mkdir(parents=True, exist_ok=True)
    else:
        d = paths.project_root() / "storage" / "status"
        d.mkdir(parents=True, exist_ok=True)
    return d / "collector.pid"


def _read_collector_pid() -> int | None:
    p = _collector_pid_path()
    try:
        raw = p.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except Exception:
        return None


def _write_collector_pid(pid: int) -> None:
    _collector_pid_path().write_text(str(int(pid)), encoding="utf-8")


def _clear_collector_pid() -> None:
    try:
        _collector_pid_path().unlink(missing_ok=True)
    except Exception:
        pass


def _is_pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        pid_i = int(pid)
    except Exception:
        return False
    if pid_i <= 0:
        return False
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid_i}", "/FO", "CSV", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            out = (proc.stdout or "").strip()
            if (not out) or out.upper().startswith("INFO:"):
                return False
            return f'"{pid_i}"' in out
        except Exception:
            return False
    try:
        os.kill(pid_i, 0)
        return True
    except PermissionError:
        return True
    except (OSError, SystemError, ValueError, TypeError):
        return False


def collector_running() -> bool:
    pid = _read_collector_pid()
    running = _is_pid_alive(pid)
    if not running and pid:
        _clear_collector_pid()
    return running


def start_collector(interval_sec: int) -> tuple[bool, str]:
    if collector_running():
        return True, "already_running"
    cmd = [
        sys.executable,
        "-m",
        "scripts.intraday_collector",
        "--interval",
        str(max(30, int(interval_sec))),
    ]
    try:
        env = os.environ.copy()
        env["FUND_ESTIMATOR_USER_ID"] = paths.current_user_id()
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen(
            cmd,
            cwd=str(paths.project_root()),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=creationflags,
        )
        _write_collector_pid(proc.pid)
        return True, f"pid={proc.pid}"
    except Exception as e:
        return False, str(e)


def ensure_collector_running() -> tuple[bool, str]:
    if not bool(getattr(settings, "COLLECTOR_AUTO_START", True)):
        return False, "auto_start_disabled"
    if collector_running():
        return True, "already_running"
    interval = int(getattr(settings, "COLLECTOR_TRADING_INTERVAL_SEC", 60) or 60)
    return start_collector(interval)
