import sys
from pathlib import Path

# ---- bootstrap: ensure project root in sys.path ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ✅ 关键：最早期初始化运行时目录（开发=项目目录；打包=AppData）
from storage import paths
from services.auth_guard import require_login
paths.ensure_dirs()

import time
import os
import signal
import subprocess
from datetime import datetime, time as dtime
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - fallback for environments without zoneinfo
    ZoneInfo = None

import streamlit as st
try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # pragma: no cover
    st_autorefresh = None

from services.watchlist_service import watchlist_list, watchlist_add, watchlist_remove
from services.estimation_service import estimate_many
from services.intraday_service import record_intraday_point


st.set_page_config(page_title="Fund Estimator", layout="wide")
require_login()

# auto refresh (Home)
HOME_AUTO_REFRESH_SEC = 30
_home_refresh_sec = st.sidebar.number_input("Home auto refresh (sec)", min_value=30, max_value=120, value=HOME_AUTO_REFRESH_SEC, step=5)
_home_auto_on = st.sidebar.checkbox("Enable home auto refresh", value=True)
if _home_auto_on:
    if st_autorefresh is not None:
        st_autorefresh(interval=int(_home_refresh_sec) * 1000, key="home_autorefresh")
    elif hasattr(st, "autorefresh"):
        st.autorefresh(interval=int(_home_refresh_sec) * 1000, key="home_autorefresh")



def _now_cn() -> datetime:
    if ZoneInfo is None:
        return datetime.now()
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _is_cn_trading_time(now: datetime) -> bool:
    """
    A股交易时段（按本机时区即可：新加坡=上海都是UTC+8）
    周一~周五：
      09:30-11:30
      13:00-15:00
    """
    if now.weekday() >= 5:  # 5=Sat,6=Sun
        return False
    t = now.time()
    return (dtime(9, 30) <= t <= dtime(11, 30)) or (dtime(13, 0) <= t <= dtime(15, 0))


def _is_close_window(now: datetime) -> bool:
    """
    收盘打点窗口：15:00:00 ~ 15:01:30
    """
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(15, 0) <= t <= dtime(15, 1, 30)


def _collector_pid_path() -> Path:
    # 兼容旧版 storage.paths（可能没有 status_dir）
    if hasattr(paths, "status_dir"):
        d = paths.status_dir()
    elif hasattr(paths, "runtime_root"):
        d = Path(paths.runtime_root()) / "status"
        d.mkdir(parents=True, exist_ok=True)
    else:
        d = PROJECT_ROOT / "storage" / "status"
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


def _collector_running() -> bool:
    pid = _read_collector_pid()
    running = _is_pid_alive(pid)
    if not running and pid:
        _clear_collector_pid()
    return running


def _start_collector(interval_sec: int, only_trading: bool) -> tuple[bool, str]:
    if _collector_running():
        return True, "already_running"
    cmd = [
        sys.executable,
        "-m",
        "scripts.intraday_collector",
        "--interval",
        str(max(30, int(interval_sec))),
    ]
    if only_trading:
        cmd.append("--only-trading")
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        _write_collector_pid(proc.pid)
        return True, f"pid={proc.pid}"
    except Exception as e:
        return False, str(e)


def _stop_collector() -> tuple[bool, str]:
    pid = _read_collector_pid()
    if not pid:
        return True, "not_running"
    try:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except Exception:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
        time.sleep(0.2)
        if _is_pid_alive(pid):
            return False, f"collector still running (pid={pid})"
        _clear_collector_pid()
        return True, "stopped"
    except Exception as e:
        return False, str(e)


def render_watchlist():
    st.title("自选基金 - 实时预估")
    flash = st.session_state.pop("_collector_flash", None)
    if isinstance(flash, dict):
        text = str(flash.get("text", "")).strip()
        icon = str(flash.get("icon", ""))
        if text:
            st.toast(text, icon=icon or None)

    st.sidebar.header("盘中采样")
    only_trading = st.sidebar.checkbox("仅交易时段采样", value=True)
    interval = st.sidebar.number_input("采样间隔（秒）", min_value=30, max_value=120, value=30, step=5)

    col_s1, col_s2 = st.sidebar.columns(2)
    running = _collector_running()

    with col_s1:
        if st.button("启动采样", width="stretch"):
            ok, msg = _start_collector(int(interval), bool(only_trading))
            if ok:
                st.session_state["_collector_flash"] = {"text": "采样已启动（独立后台进程）", "icon": "🟢"}
            else:
                st.session_state["_collector_flash"] = {"text": f"采样启动失败：{msg}", "icon": "❌"}
            st.rerun()

    with col_s2:
        if st.button("停止采样", width="stretch"):
            ok, msg = _stop_collector()
            if ok:
                st.session_state["_collector_flash"] = {"text": "采样已停止", "icon": "🛑"}
            else:
                st.session_state["_collector_flash"] = {"text": f"停止失败：{msg}", "icon": "❌"}
            st.rerun()

    st.sidebar.caption(
        f"采样状态：{'运行中' if running else '未运行'}。采样为独立后台进程，切换到其他 tab 也会继续。"
    )

    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        code = st.text_input("新增基金代码", value="", placeholder="例如：510300 / 000001")

    with col2:
        if st.button("添加", width="stretch"):
            if code.strip():
                watchlist_add(code.strip())
                st.toast("已添加", icon="✅")
                st.rerun()

    with col3:
        if st.button("刷新估值", width="stretch"):
            st.rerun()

    codes = watchlist_list()
    if not codes:
        st.info("自选为空：输入代码点击添加。")
        return

    est_map = estimate_many(codes)

    # lightweight sampling on Home rerun (persists to intraday)
    if "home_last_sample_ts" not in st.session_state:
        st.session_state["home_last_sample_ts"] = {}
    _last_map = st.session_state["home_last_sample_ts"]
    _now = _now_cn()
    _ds = _now.date().isoformat()
    _last_ts = float(_last_map.get("_all", 0.0) or 0.0)
    if (_now.timestamp() - _last_ts) >= max(30, int(_home_refresh_sec)):
        for _c, _est in est_map.items():
            if _est:
                record_intraday_point(target=_c, estimate=_est, date_str=_ds)
        _last_map["_all"] = _now.timestamp()
        st.session_state["home_last_sample_ts"] = _last_map

    rows = []
    for c in codes:
        est = est_map.get(c)
        if not est:
            rows.append(
                {
                    "code": c,
                    "name": f"基金{c}",
                    "est_nav": "",
                    "pct": "",
                    "time": "",
                    "method": "",
                    "conf": "",
                    "warn": "无估值数据",
                }
            )
        else:
            rows.append(
                {
                    "code": c,
                    "name": est.name,
                    "est_nav": est.est_nav,
                    "pct": f"{est.est_change_pct:.2f}%",
                    "time": est.est_time,
                    "method": est.method,
                    "conf": est.confidence,
                    "warn": est.warning or "",
                }
            )

    st.caption(f"更新时间：{datetime.now().isoformat(timespec='seconds')}（本页刷新不会写入日结，仅展示）")
    st.dataframe(rows, width="stretch", hide_index=True)

    name_map = {c: ((est_map.get(c).name if est_map.get(c) else '') or f'\u57fa\u91d1{c}') for c in codes}

    def _fmt_code(c: str) -> str:
        return f"{c} - {name_map.get(c, '')}"

    st.divider()
    st.subheader("查看基金详情")
    sel = st.selectbox("\u9009\u62e9\u4e00\u4e2a\u57fa\u91d1\u6253\u5f00\u8be6\u60c5\u9875", options=codes, format_func=_fmt_code)
    if st.button("打开详情页", width="stretch"):
        try:
            st.query_params["code"] = sel  # 新版
        except Exception:
            st.experimental_set_query_params(code=sel)  # 旧版
        # ✅ 注意：switch_page 的路径必须相对 app/ 目录
        st.switch_page("pages/03_基金详情.py")

    st.divider()
    st.subheader("管理自选")
    rm_code = st.selectbox("\u9009\u62e9\u8981\u79fb\u9664\u7684\u4ee3\u7801", options=codes, key="rm_code", format_func=_fmt_code)
    if st.button("移除所选", type="secondary", width="stretch"):
        watchlist_remove(rm_code)
        st.toast("已移除", icon="🗑️")
        st.rerun()


render_watchlist()
