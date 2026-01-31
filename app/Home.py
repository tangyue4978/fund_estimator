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

import streamlit as st

from services.watchlist_service import watchlist_list, watchlist_add, watchlist_remove
from services.estimation_service import estimate_many
from services.intraday_service import record_intraday_point, intraday_append_close_marker


st.set_page_config(page_title="Fund Estimator", layout="wide")


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


def _collector_loop(interval_sec: int, only_trading: bool = True):
    """
    后台采样：
      - only_trading=True 时，仅交易时段采样
      - 15:00 附近自动写 CLOSE 标记点（每基金每天仅一次）
    注意：不要在这个线程里调用 st.xxx
    """
    from datetime import date as _date

    while st.session_state.get("_collector_running", False):
        now = datetime.now()
        ds = _date.today().isoformat()

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

    st.divider()
    st.subheader("查看基金详情")

    sel = st.selectbox("选择一个基金打开详情页", options=codes)
    if st.button("打开详情页", width="stretch"):
        try:
            st.query_params["code"] = sel  # 新版
        except Exception:
            st.experimental_set_query_params(code=sel)  # 旧版
        # ✅ 注意：switch_page 的路径必须相对 app/ 目录
        st.switch_page("pages/03_Fund_Detail.py")

    st.divider()
    st.subheader("管理自选")

    rm_code = st.selectbox("选择要移除的代码", options=codes, key="rm_code")
    if st.button("移除所选", type="secondary", width="stretch"):
        watchlist_remove(rm_code)
        st.toast("已移除", icon="🗑️")
        st.rerun()


render_watchlist()
