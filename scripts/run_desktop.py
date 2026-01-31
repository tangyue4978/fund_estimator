from __future__ import annotations

import os
import sys
import time
import socket
import threading
import traceback
import subprocess
from pathlib import Path
import webbrowser


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _runtime_root() -> Path:
    """
    开发环境：项目根目录
    打包(one-dir)环境：exe 所在目录 (dist/FundEstimator)
    """
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


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
    """
    开发环境：用 subprocess 起采集器（稳）
    """
    cmd = [sys.executable, "-m", "scripts.intraday_collector", "--interval", "10", "--only-trading"]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(cmd, cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)


def _run_collector_inproc() -> None:
    """
    打包环境：不要再用 sys.executable 起子进程（会变成起自己 exe）
    直接在本进程循环跑采集器逻辑：最稳
    """
    try:
        from scripts.intraday_collector import main as collector_main
        # interval=10 only-trading 常驻
        collector_main(["--interval", "10", "--only-trading"])
    except Exception:
        # 采集器挂了不影响 UI
        pass


def _run_streamlit_inproc(root: Path, port: int) -> None:
    """
    打包环境：在同进程后台线程启动 Streamlit（不要 subprocess）
    """
    try:
        # 关键：用 streamlit 的 CLI 入口在进程内启动
        from streamlit.web import cli as stcli

        # app/Home.py 必须是“真实路径”
        app_path = root / "app" / "Home.py"
        if not app_path.exists():
            # 兜底：有些情况下资源可能被放到 _internal 里
            alt = root / "_internal" / "app" / "Home.py"
            if alt.exists():
                app_path = alt

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
        # 失败就打印到日志/控制台（打包时你可能看不到，后面可写日志）
        traceback.print_exc()


def main() -> int:
    root = _runtime_root()
    host, port = "127.0.0.1", 8501
    url = f"http://{host}:{port}"

    # 单实例锁：锁文件放到 exe 同级的 storage/status 下
    lock_path = root / "storage" / "status" / "desktop.lock"
    if not _acquire_lock(lock_path):
        # 已有实例：如果服务已在，直接打开浏览器到已有服务
        if _is_port_open(host, port):
            webbrowser.open(url)
        return 0

    # 如果已经有服务（比如你手动起过 streamlit），就不重复启动
    if not _is_port_open(host, port):
        if _is_frozen():
            # 打包环境：streamlit + collector 都用线程 in-proc 启动
            t1 = threading.Thread(target=_run_streamlit_inproc, args=(root, port), daemon=True)
            t1.start()

            t2 = threading.Thread(target=_run_collector_inproc, daemon=True)
            t2.start()
        else:
            # 开发环境：继续用 subprocess
            _spawn_collector_dev(root)

            # 开发环境 streamlit 还是用 subprocess（避免占用主线程）
            cmd = [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(root / "app" / "Home.py"),
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
            subprocess.Popen(cmd, cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)

        # 等端口起来
        for _ in range(250):  # 25s
            if _is_port_open(host, port):
                break
            time.sleep(0.1)

    # 如果还是没起来：退化开浏览器，避免“没反应”
    if not _is_port_open(host, port):
        webbrowser.open(url)
        return 0

    # 打开桌面窗口
    import webview
    webview.create_window("Fund Estimator", url=url, width=1200, height=800, min_size=(1000, 700))
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
