import sys
from pathlib import Path

# ---- bootstrap: ensure project root in sys.path ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ✅ 关键：最早期初始化运行时目录（开发=项目目录；打包=AppData）
from storage import paths
from services.auth_guard import require_login
from config import settings
paths.ensure_dirs()

import time
import os
import signal
import subprocess
from datetime import datetime, time as dtime

import streamlit as st
try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # pragma: no cover
    st_autorefresh = None

from services.watchlist_service import watchlist_list, watchlist_add, watchlist_remove
try:
    from services.watchlist_service import watchlist_add_result
except Exception:  # backward-compatible fallback for mixed deploy states
    watchlist_add_result = None
from services.estimation_service import estimate_many
from services.intraday_service import record_intraday_point
from services.trading_time import now_cn, is_cn_trading_time


st.set_page_config(page_title="Fund Estimator", layout="wide")
require_login()


def _apply_silent_autorefresh_style() -> None:
    if not bool(getattr(settings, "SILENT_AUTO_REFRESH_UI", True)):
        return
    st.markdown(
        """
<style>
[data-testid="stStatusWidget"] { display: none !important; }
</style>
        """,
        unsafe_allow_html=True,
    )


# auto refresh (Home) - code-only config
_home_auto_on = bool(getattr(settings, "HOME_AUTO_REFRESH_ENABLED", True))
_home_refresh_raw = getattr(settings, "HOME_AUTO_REFRESH_SEC", 30)
_home_refresh_sec = int(30 if _home_refresh_raw is None else _home_refresh_raw)
if _home_auto_on and _home_refresh_sec > 0:
    _apply_silent_autorefresh_style()
    if st_autorefresh is not None:
        st_autorefresh(interval=int(_home_refresh_sec) * 1000, key="home_autorefresh")
    elif hasattr(st, "autorefresh"):
        st.autorefresh(interval=int(_home_refresh_sec) * 1000, key="home_autorefresh")



def _is_close_window(now: datetime) -> bool:
    """
    收盘打点窗口：15:00:00 ~ 15:01:30
    """
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(15, 0) <= t <= dtime(15, 1, 30)


def _collector_pid_path() -> Path:
    if hasattr(paths, "file_collector_pid"):
        return Path(paths.file_collector_pid())
    # 兼容旧版 storage.paths（可能没有 status_dir）
    if hasattr(paths, "status_dir"):
        d = paths.status_dir()
    elif hasattr(paths, "runtime_root"):
        d = Path(paths.runtime_root()) / "status"
        d.mkdir(parents=True, exist_ok=True)
    else:
        d = PROJECT_ROOT / "storage" / "status"
        d.mkdir(parents=True, exist_ok=True)
    uid = str(paths.current_user_id()).strip() or "public"
    return d / f"collector_{uid}.pid"


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
        # Ensure collector writes to the current user's data directory.
        env = os.environ.copy()
        env["FUND_ESTIMATOR_USER_ID"] = paths.current_user_id()
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
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

    st.sidebar.header("采样状态")
    running = _collector_running()
    only_trading = True
    interval = int(getattr(settings, "COLLECTOR_TRADING_INTERVAL_SEC", 60) or 60)
    off_interval = int(getattr(settings, "COLLECTOR_OFFMARKET_INTERVAL_SEC", 1800) or 1800)

    if (not running) and bool(getattr(settings, "COLLECTOR_AUTO_START", True)):
        ok, msg = _start_collector(interval, False)
        running = ok
        if ok:
            st.session_state["_collector_flash"] = {"text": "采样已自动启动", "icon": "✅"}
        else:
            st.session_state["_collector_flash"] = {"text": f"采样自动启动失败：{msg}", "icon": "❌"}

    st.sidebar.caption(
        f"采样状态：{'运行中' if running else '未运行'}。"
        f"盘中每 {interval}s，非交易时段每 {int(off_interval/60)} 分钟刷新净值。"
    )

    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        code = st.text_input("新增基金代码", value="", placeholder="例如：510300 / 000001")

    with col2:
        if st.button("添加", width="stretch"):
            if code.strip():
                if callable(watchlist_add_result):
                    res = watchlist_add_result(code.strip())
                    if bool(res.get("ok")):
                        msg = str(res.get("message", "已添加"))
                        icon = "✅" if bool(res.get("cloud_synced", False)) else "⚠️"
                        st.toast(msg, icon=icon)
                    else:
                        st.toast(str(res.get("message", "添加失败")), icon="❌")
                else:
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

    # Silent page-side sampling write: write after fetch, no toast/rerun.
    if "home_last_sample_ts" not in st.session_state:
        st.session_state["home_last_sample_ts"] = {}
    _last_map = st.session_state["home_last_sample_ts"]
    _now = now_cn()
    _last_ts = float(_last_map.get("_all", 0.0) or 0.0)
    _can_sample = (not bool(only_trading)) or is_cn_trading_time(_now)
    if (not running) and _can_sample and (_now.timestamp() - _last_ts) >= max(30, int(interval)):
        _ds = _now.date().isoformat()
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
