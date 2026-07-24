import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage import paths
from services.auth_guard import require_login
from config import constants, settings

paths.ensure_dirs()

import streamlit as st
import pandas as pd

from app.ui import (
    apply_app_style,
    configure_page,
    danger_container,
    dataframe_height,
    degraded_notice,
    empty_state,
    estimate_method_label,
    page_header,
    section_header,
)
from services.cloud_status_service import get_cloud_error
from services.estimation_service import estimate_many
from services.fund_service import get_fund_profile
from services.trading_time import cn_market_phase, now_cn
from services.watchlist_service import watchlist_add, watchlist_list, watchlist_remove

try:
    from services.watchlist_service import watchlist_add_result
except Exception:  # pragma: no cover
    watchlist_add_result = None


configure_page("自选与估值", icon="📊")
apply_app_style()
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
WATCHLIST_COLUMN_CONFIG = {
    "code": "基金代码",
    "name": "基金名称",
    "est_nav": st.column_config.NumberColumn("估算净值", format="%.6f"),
    "pct": "估算涨跌",
    "time": "估值时间",
    "method": "估值方式",
    "conf": st.column_config.NumberColumn("置信度", format="%.2f"),
    "warn": "提示",
}


def _render_watchlist_live(codes: list[str], sort_by: str = "默认", warn_only: bool = False) -> dict:
    rows, est_map = _build_watchlist_rows(codes)
    realtime_estimates = [
        est
        for est in est_map.values()
        if est
        and float(est.est_nav or 0.0) > 0
        and est.method != constants.METHOD_FROZEN_NAV
    ]
    avg_pct = (
        sum(float(est.est_change_pct or 0.0) for est in realtime_estimates) / len(realtime_estimates)
        if realtime_estimates
        else None
    )
    up_count = sum(1 for est in realtime_estimates if float(est.est_change_pct or 0.0) > 0)
    down_count = sum(1 for est in realtime_estimates if float(est.est_change_pct or 0.0) < 0)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("自选数量", len(codes))
    m2.metric("实时覆盖", f"{len(realtime_estimates)}/{len(codes)}")
    m3.metric("平均涨跌幅", f"{avg_pct:.2f}%" if avg_pct is not None else "暂无")
    m4.metric("上涨/下跌", f"{up_count}/{down_count}" if realtime_estimates else "暂无")

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
        rows = sorted(rows, key=_row_conf, reverse=True)

    st.caption(f"更新时间：{now_cn().strftime('%Y-%m-%d %H:%M:%S')} · 仅展示线上估值")
    for row in rows:
        row["method"] = estimate_method_label(row.get("method"))
    table_height = dataframe_height(len(rows), min_rows=2, max_rows=12)
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
            column_config=WATCHLIST_COLUMN_CONFIG,
        )
    else:
        empty_state(
            "没有符合当前筛选条件的基金",
            "关闭“只看有提示的基金”或调整排序条件后再试。",
        )
    return est_map


def render_watchlist() -> None:
    page_header(
        "自选与估值",
        "集中查看关注基金的盘中估值、数据覆盖和风险提示。",
        eyebrow="基金估值工作台",
    )
    st.sidebar.caption("交易时段内会自动刷新列表估值。")
    codes = watchlist_list()
    watchlist_err = get_cloud_error("watchlist")
    if watchlist_err:
        degraded_notice(
            "云端自选列表暂时不可用，当前可能显示最近一次成功读取的数据。",
            watchlist_err,
            detail_label="自选列表技术详情",
        )

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        code = st.text_input(
            "新增基金代码",
            value="",
            placeholder="例如：510300 或 000001",
            help="请输入 6 位基金代码。",
        )
        code_is_valid = not code.strip() or (code.strip().isdigit() and len(code.strip()) == 6)
        if code.strip() and not code_is_valid:
            st.error("基金代码应为 6 位数字。")
    with col2:
        if st.button(
            "添加到自选",
            type="primary",
            width="stretch",
            disabled=not code.strip() or not code_is_valid,
        ):
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
        if st.button(
            "刷新估值",
            width="stretch",
            help="立即重新获取当前自选基金估值。",
            disabled=not codes,
        ):
            _clear_home_est_cache()
            st.rerun()

    if not codes:
        empty_state(
            "自选列表还是空的",
            "在上方输入 6 位基金代码并添加，即可开始查看盘中估值。",
        )
        return

    sort_by = st.radio("排序方式", ["默认顺序", "涨跌幅", "置信度"], horizontal=True)
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
    section_header("查看基金详情", "选择一只基金，查看走势、估值误差和日结覆盖状态。")
    selected = st.selectbox("选择一个基金打开详情页", options=codes, format_func=_fmt_code)
    if st.button("查看所选基金详情", type="primary", width="stretch"):
        st.query_params["code"] = selected
        st.switch_page(DETAIL_PAGE)

    st.divider()
    with st.expander("管理自选列表", expanded=False):
        rm_code = st.selectbox("要移除的基金", options=codes, key="rm_code", format_func=_fmt_code)
        with danger_container("remove_watchlist"):
            st.caption("此操作只会移出自选列表，不会删除持仓或日结记录。")
            if st.button("从自选中移除", type="primary", width="stretch"):
                res = watchlist_remove(rm_code)
                if bool(res.get("ok", True)):
                    st.toast("已从自选中移除", icon="🗑️")
                    _clear_home_est_cache()
                    st.rerun()
                else:
                    st.toast(str(res.get("message", "移除失败")), icon="❌")


WATCHLIST_PAGE = st.Page(render_watchlist, title="自选与估值", icon=":material/home:", default=True)
PORTFOLIO_PAGE = st.Page(
    str(APP_DIR / "pages" / "01_持仓.py"),
    title="持仓管理",
    icon=":material/account_balance_wallet:",
)
LEDGER_PAGE = st.Page(
    str(APP_DIR / "pages" / "02_日结.py"),
    title="日结台账",
    icon=":material/receipt_long:",
)
DETAIL_PAGE = st.Page(
    str(APP_DIR / "pages" / "03_基金详情.py"),
    title="基金详情",
    icon=":material/finance:",
)
ANALYSIS_PAGE = st.Page(
    str(APP_DIR / "pages" / "04_组合分析.py"),
    title="组合分析",
    icon=":material/analytics:",
)
SYSTEM_STATUS_PAGE = st.Page(
    str(APP_DIR / "pages" / "05_系统状态.py"),
    title="系统状态",
    icon=":material/health_and_safety:",
)

navigation = st.navigation(
    [
        WATCHLIST_PAGE,
        PORTFOLIO_PAGE,
        LEDGER_PAGE,
        DETAIL_PAGE,
        ANALYSIS_PAGE,
        SYSTEM_STATUS_PAGE,
    ]
)
navigation.run()
