import sys
from pathlib import Path

# ---- bootstrap: ensure project root in sys.path ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ✅ 关键：最早期初始化运行时目录（开发=项目目录；打包=AppData）
from storage import paths
paths.ensure_dirs()

import time
import threading
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
from services.intraday_service import record_intraday_point, intraday_append_close_marker
from storage.json_store import update_json, load_json


st.set_page_config(page_title="Fund Estimator", layout="wide")

# auto refresh (Home)
HOME_AUTO_REFRESH_SEC = 10
_home_refresh_sec = st.sidebar.number_input("Home auto refresh (sec)", min_value=5, max_value=120, value=HOME_AUTO_REFRESH_SEC, step=5)
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


def _status_path() -> str:
    return str(paths.data_dir() / "intraday_status.json")


def _write_collector_status(payload: dict) -> None:
    p = _status_path()
    def updater(data: dict):
        data.update(payload)
        return data
    update_json(p, updater)


def _read_collector_status() -> dict:
    return load_json(_status_path(), fallback={}) or {}

def _collector_loop(interval_sec: int, only_trading: bool = True):
    """
    后台采样：
      - only_trading=True 时，仅交易时段采样
      - 15:00 附近自动写 CLOSE 标记点（每基金每天仅一次）
    注意：不要在这个线程里调用 st.xxx
    """
    while st.session_state.get("_collector_running", False):
        now = _now_cn()
        ds = now.date().isoformat()
        wrote_points = 0

        try:
            codes = watchlist_list()

            # 1) 收盘标记点（15:00 窗口）
            if codes and _is_close_window(now):
                est_map = estimate_many(codes)
                for c in codes:
                    est = est_map.get(c)
                    intraday_append_close_marker(
                        target=c,
                        estimate=est,
                        date_str=ds,
                    )

            # 2) 盘中采样
            if codes and (not only_trading or _is_cn_trading_time(now)):
                est_map = estimate_many(codes)
                for c in codes:
                    est = est_map.get(c)
                    if not est:
                        continue
                    record_intraday_point(
                        target=c,
                        estimate=est,
                        date_str=ds,
                    )
                    wrote_points += 1

        except Exception:
            # 避免线程异常导致整个采样停掉
            pass

        time.sleep(max(3, int(interval_sec)))


def render_watchlist():
    st.title("自选基金 - 实时预估")

    st.sidebar.header("盘中采样")
    only_trading = st.sidebar.checkbox("仅交易时段采样", value=True)
    interval = st.sidebar.number_input("采样间隔（秒）", min_value=5, max_value=120, value=10, step=5)

    if "_collector_running" not in st.session_state:
        st.session_state["_collector_running"] = False

    col_s1, col_s2 = st.sidebar.columns(2)

    with col_s1:
        if st.button("启动采样", width="stretch"):
            if not st.session_state["_collector_running"]:
                st.session_state["_collector_running"] = True
                th = threading.Thread(
                    target=_collector_loop,
                    args=(int(interval), bool(only_trading)),
                    daemon=True,
                )
                st.session_state["_collector_thread"] = th
                th.start()
                st.toast("采样已启动", icon="🟢")

    with col_s2:
        if st.button("停止采样", width="stretch"):
            st.session_state["_collector_running"] = False
            st.toast("采样已停止", icon="🛑")

    st.sidebar.caption("提示：采样依赖页面会话；关闭浏览器/停止 Streamlit 会停止采样。")

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
    if (_now.timestamp() - _last_ts) >= max(5, int(_home_refresh_sec)):
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
