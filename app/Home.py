import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage import paths
from services.auth_guard import require_login
from config import settings

paths.ensure_dirs()

import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # pragma: no cover
    st_autorefresh = None

from services.estimation_service import estimate_many
from services.cloud_status_service import get_cloud_error
from services.trading_time import now_cn
from services.watchlist_service import watchlist_add, watchlist_list, watchlist_remove

try:
    from services.watchlist_service import watchlist_add_result
except Exception:  # pragma: no cover
    watchlist_add_result = None


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


def _home_refresh_sec() -> int:
    raw = getattr(settings, "HOME_AUTO_REFRESH_SEC", 60)
    try:
        return max(0, int(raw))
    except Exception:
        return 60


if bool(getattr(settings, "HOME_AUTO_REFRESH_ENABLED", True)):
    refresh_sec = _home_refresh_sec()
    if refresh_sec > 0:
        _apply_silent_autorefresh_style()
        if st_autorefresh is not None:
            st_autorefresh(interval=refresh_sec * 1000, key="home_autorefresh")
        elif hasattr(st, "autorefresh"):
            st.autorefresh(interval=refresh_sec * 1000, key="home_autorefresh")


def render_watchlist() -> None:
    st.title("自选基金 - 实时预估")
    st.sidebar.caption("网页版按页面自动刷新展示估值。")
    watchlist_err = get_cloud_error("watchlist")
    if watchlist_err:
        st.warning(f"自选列表读取失败，当前页面可能显示为空数据：{watchlist_err}")

    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        code = st.text_input("新增基金代码", value="", placeholder="例如：510300 / 000001")

    with col2:
        if st.button("添加", width="stretch") and code.strip():
            if callable(watchlist_add_result):
                res = watchlist_add_result(code.strip())
                if bool(res.get("ok")):
                    st.toast(str(res.get("message", "已添加")), icon="✅")
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
    rows = []
    for code_item in codes:
        est = est_map.get(code_item)
        if not est:
            rows.append(
                {
                    "code": code_item,
                    "name": f"基金{code_item}",
                    "est_nav": "",
                    "pct": "",
                    "time": "",
                    "method": "",
                    "conf": "",
                    "warn": "无估值数据",
                }
            )
            continue
        rows.append(
            {
                "code": code_item,
                "name": est.name,
                "est_nav": est.est_nav,
                "pct": f"{est.est_change_pct:.2f}%",
                "time": est.est_time,
                "method": est.method,
                "conf": est.confidence,
                "warn": est.warning or "",
            }
        )

    st.caption(f"更新时间：{now_cn().isoformat(timespec='seconds')}（仅展示线上估值）")
    st.dataframe(rows, width="stretch", hide_index=True)

    name_map = {c: ((est_map.get(c).name if est_map.get(c) else "") or f"基金{c}") for c in codes}

    def _fmt_code(code_item: str) -> str:
        return f"{code_item} - {name_map.get(code_item, '')}"

    st.divider()
    st.subheader("查看基金详情")
    selected = st.selectbox("选择一个基金打开详情页", options=codes, format_func=_fmt_code)
    if st.button("打开详情页", width="stretch"):
        try:
            st.query_params["code"] = selected
        except Exception:
            try:
                params = st.experimental_get_query_params()
                params["code"] = [selected]
                st.experimental_set_query_params(**params)
            except Exception:
                st.experimental_set_query_params(code=selected)
        st.switch_page("pages/03_基金详情.py")

    st.divider()
    st.subheader("管理自选")
    rm_code = st.selectbox("选择要移除的代码", options=codes, key="rm_code", format_func=_fmt_code)
    if st.button("移除所选", type="secondary", width="stretch"):
        res = watchlist_remove(rm_code)
        if bool(res.get("ok", True)):
            st.toast("已移除", icon="🗑️")
            st.rerun()
        else:
            st.toast(str(res.get("message", "移除失败")), icon="❌")


render_watchlist()
