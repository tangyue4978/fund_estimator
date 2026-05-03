import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage import paths
from services.auth_guard import require_login
from config import settings

paths.ensure_dirs()

import streamlit as st
import pandas as pd

from services.cloud_status_service import get_cloud_error
from services.estimation_service import estimate_many
from services.fund_service import get_fund_profile
from services.trading_time import cn_market_phase, now_cn
from services.watchlist_service import watchlist_add, watchlist_list, watchlist_remove

try:
    from services.watchlist_service import watchlist_add_result
except Exception:  # pragma: no cover
    watchlist_add_result = None


st.set_page_config(page_title="Fund Estimator", layout="wide")
require_login()


def _home_refresh_sec() -> int:
    phase = cn_market_phase(now_cn())
    if phase == "trading":
        raw = getattr(settings, "HOME_AUTO_REFRESH_SEC", 60)
    else:
        raw = getattr(settings, "HOME_AUTO_REFRESH_SEC_NON_TRADING", 1800)
    try:
        return max(0, int(raw))
    except Exception:
        return 60 if phase == "trading" else 1800


def _home_fragment_refresh_enabled() -> bool:
    auto_on = bool(getattr(settings, "HOME_AUTO_REFRESH_ENABLED", True))
    return auto_on and _home_refresh_sec() > 0 and hasattr(st, "fragment")


def _clear_home_est_cache() -> None:
    st.session_state.pop("_home_est_cache", None)


def _get_home_est_map(codes: list[str]) -> dict:
    key = tuple(codes)
    now_ts = time.time()
    cache = st.session_state.get("_home_est_cache")
    if isinstance(cache, dict) and cache.get("key") == key and (now_ts - float(cache.get("ts", 0.0))) <= 8.0:
        est_map = cache.get("est_map", {})
        return est_map if isinstance(est_map, dict) else {}
    est_map = estimate_many(codes)
    st.session_state["_home_est_cache"] = {"key": key, "ts": now_ts, "est_map": est_map}
    return est_map


def _build_watchlist_rows(codes: list[str]) -> tuple[list[dict], dict]:
    est_map = _get_home_est_map(codes)
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
                    "warn": "暂无估值数据",
                }
            )
            continue
        rows.append(
            {
                "code": code_item,
                "name": str(est.name or "").strip() or f"基金{code_item}",
                "est_nav": est.est_nav,
                "pct": f"{est.est_change_pct:.2f}%",
                "time": est.est_time,
                "method": est.method,
                "conf": est.confidence,
                "warn": est.warning or "",
            }
        )
    return rows, est_map


WATCHLIST_COLUMNS = ["code", "name", "est_nav", "pct", "time", "method", "conf", "warn"]


def _render_watchlist_live(codes: list[str], sort_by: str = "默认", warn_only: bool = False) -> dict:
    rows, est_map = _build_watchlist_rows(codes)
    valid_estimates = [est for est in est_map.values() if est]
    if valid_estimates:
        avg_pct = sum(float(est.est_change_pct or 0.0) for est in valid_estimates) / len(valid_estimates)
        up_count = sum(1 for est in valid_estimates if float(est.est_change_pct or 0.0) > 0)
        down_count = sum(1 for est in valid_estimates if float(est.est_change_pct or 0.0) < 0)
        warn_count = sum(1 for est in valid_estimates if str(est.warning or "").strip())
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("自选数量", len(codes))
        m2.metric("平均涨跌幅", f"{avg_pct:.2f}%")
        m3.metric("上涨/下跌", f"{up_count}/{down_count}")
        m4.metric("提示数", warn_count)

    def _row_pct(row: dict) -> float:
        try:
            return float(str(row.get("pct", "0")).replace("%", "") or 0.0)
        except Exception:
            return 0.0

    def _row_conf(row: dict) -> float:
        try:
            return float(row.get("conf", 0.0) or 0.0)
        except Exception:
            return 0.0

    if warn_only:
        rows = [row for row in rows if str(row.get("warn", "") or "").strip()]
    if sort_by == "涨跌幅":
        rows = sorted(rows, key=_row_pct, reverse=True)
    elif sort_by == "置信度":
        rows = sorted(rows, key=_row_conf)

    st.caption(f"更新时间：{now_cn().isoformat(timespec='seconds')}（仅展示线上估值）")
    row_height = 35
    header_height = 38
    table_height = header_height + max(1, len(rows)) * row_height
    if rows:
        def _color_pct(row: dict) -> list[str]:
            pct = _row_pct(row)
            color = "#d92d20" if pct > 0 else ("#039855" if pct < 0 else "#344054")
            return [f"color: {color}" if col == "pct" else "" for col in row.index]

        st.dataframe(
            pd.DataFrame(rows, columns=WATCHLIST_COLUMNS).style.apply(_color_pct, axis=1),
            width="stretch",
            hide_index=True,
            height=table_height,
        )
    else:
        st.dataframe(pd.DataFrame(rows, columns=WATCHLIST_COLUMNS), width="stretch", hide_index=True, height=table_height)
    return est_map


def render_watchlist() -> None:
    st.title("自选基金 - 实时估值")
    st.sidebar.caption("列表估值数据会按交易时段自动局部刷新。")
    watchlist_err = get_cloud_error("watchlist")
    if watchlist_err:
        st.warning(f"自选列表读取失败，当前显示最近一次成功读取的数据：{watchlist_err}")

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
            _clear_home_est_cache()
            st.rerun()
    with col3:
        if st.button("刷新估值", width="stretch"):
            _clear_home_est_cache()
            st.rerun()

    codes = watchlist_list()
    if not codes:
        st.info("自选为空：输入代码后点击添加。")
        return

    sort_by = st.radio("列表排序", ["默认", "涨跌幅", "置信度"], horizontal=True)
    warn_only = st.toggle("只看有提示的基金", value=False)
    refresh_sec = _home_refresh_sec()
    use_fragment_refresh = _home_fragment_refresh_enabled()
    est_map = {}
    live_watchlist_area = st.container()
    if use_fragment_refresh:

        @st.fragment(run_every=f"{refresh_sec}s")
        def _live_watchlist_fragment() -> None:
            with live_watchlist_area:
                _render_watchlist_live(codes, sort_by=sort_by, warn_only=warn_only)

        _live_watchlist_fragment()
        _, est_map = _build_watchlist_rows(codes)
    else:
        with live_watchlist_area:
            est_map = _render_watchlist_live(codes, sort_by=sort_by, warn_only=warn_only)

    def _fund_name(code_item: str) -> str:
        est = est_map.get(code_item)
        name = (est.name if est else "") or ""
        if name:
            return name
        try:
            profile = get_fund_profile(code_item)
            return (profile.name or "").strip() or f"基金{code_item}"
        except Exception:
            return f"基金{code_item}"

    name_map = {c: _fund_name(c) for c in codes}

    def _fmt_code(code_item: str) -> str:
        return f"{code_item} - {name_map.get(code_item, '')}"

    st.divider()
    st.subheader("查看基金详情")
    selected = st.selectbox("选择一个基金打开详情页", options=codes, format_func=_fmt_code)
    if st.button("打开详情页", width="stretch"):
        st.query_params["code"] = selected
        st.switch_page("pages/03_基金详情.py")

    st.divider()
    st.subheader("管理自选")
    rm_code = st.selectbox("选择要移除的代码", options=codes, key="rm_code", format_func=_fmt_code)
    if st.button("移除所选", type="secondary", width="stretch"):
        res = watchlist_remove(rm_code)
        if bool(res.get("ok", True)):
            st.toast("已移除", icon="🗑️")
            _clear_home_est_cache()
            st.rerun()
        else:
            st.toast(str(res.get("message", "移除失败")), icon="❌")


render_watchlist()
