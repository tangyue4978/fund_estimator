import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---- bootstrap: ensure project root in sys.path ----
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import settings
from services.accuracy_service import fund_gap_summary, fund_gap_table, guess_gap_reasons
from services.auth_guard import require_login
from services.chart_service import (
    CHART_MY_PROFIT,
    CHART_OFFICIAL_NAV,
    CHART_REALTIME_EST,
    get_chart_data,
)
from services.cloud_status_service import get_cloud_error
from services.estimation_service import estimate_one
from services.fund_service import get_fund_profile
from services.settlement_service import get_ledger_row
from services.trading_time import cn_market_phase, now_cn
from services.watchlist_service import watchlist_list


st.set_page_config(page_title="Fund Detail", layout="wide")
require_login()


def _fund_detail_refresh_sec() -> int:
    phase = cn_market_phase(now_cn())
    if phase == "trading":
        refresh_raw = getattr(settings, "FUND_DETAIL_AUTO_REFRESH_SEC", 30)
    elif phase == "lunch":
        refresh_raw = getattr(settings, "FUND_DETAIL_AUTO_REFRESH_SEC_LUNCH", 300)
    else:
        refresh_raw = getattr(settings, "FUND_DETAIL_AUTO_REFRESH_SEC_NON_TRADING", 900)
    try:
        return max(0, int(refresh_raw))
    except Exception:
        return 30 if phase == "trading" else (300 if phase == "lunch" else 900)


def _fund_detail_fragment_refresh_enabled() -> bool:
    auto_on = bool(getattr(settings, "FUND_DETAIL_AUTO_REFRESH_ENABLED", True))
    return auto_on and _fund_detail_refresh_sec() > 0 and hasattr(st, "fragment")


def _pick_code_from_query_or_select() -> str:
    code = ""
    try:
        qp = st.query_params
        code = qp.get("code", "")
        if isinstance(code, list):
            code = code[0] if code else ""
    except Exception:
        code = ""

    code = (code or "").strip()
    options = list(watchlist_list())
    if code and code not in options:
        options = [code] + options

    def _fmt_option(c: str) -> str:
        try:
            p = get_fund_profile(c)
            n = (p.name or "").strip()
        except Exception:
            n = ""
        if not n:
            n = f"基金{c}"
        return f"{c} - {n}"

    if not options:
        return st.text_input("基金代码", value="", placeholder="例如：510300 / 000001").strip()
    return st.selectbox("基金代码", options=options, format_func=_fmt_option)


def _render_live_estimate_and_chart(code: str, chart_type: str, range_value: str) -> None:
    try:
        est = estimate_one(code)
    except Exception:
        est = None
        st.error("估值获取失败，请稍后刷新重试。")

    c1, c2, c3, c4, c5 = st.columns([2, 1, 1, 1, 2])
    with c1:
        st.metric("名称", est.name if est else f"基金{code}")
    with c2:
        st.metric("预估净值", f"{est.est_nav:.6f}" if est else "-")
    with c3:
        st.metric("预估涨跌幅", f"{est.est_change_pct:.2f}%" if est else "-")
    with c4:
        st.metric("置信度", f"{est.confidence:.2f}" if est else "-")
    with c5:
        st.caption(f"估值时间：{est.est_time if est else '-'} | 方法：{est.method if est else '-'}")

    if est and est.warning:
        st.warning(est.warning)

    st.divider()
    st.subheader("走势")
    chart_type_map = {
        "官方净值": CHART_OFFICIAL_NAV,
        "实时估值": CHART_REALTIME_EST,
        "我的收益": CHART_MY_PROFIT,
    }
    points = get_chart_data(code, chart_type_map[chart_type], range_value)
    if not points:
        st.info("当前条件下暂无走势数据。")
        return

    df_chart = pd.DataFrame(points)
    line_color = "#344054"
    try:
        first_value = float(df_chart["value"].iloc[0])
        last_value = float(df_chart["value"].iloc[-1])
        if last_value > first_value:
            line_color = "#d92d20"
        elif last_value < first_value:
            line_color = "#039855"
    except Exception:
        pass
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df_chart["date"],
            y=df_chart["value"],
            mode="lines",
            line=dict(width=2, color=line_color),
            name=chart_type,
            hovertemplate="日期: %{x}<br>数值: %{y}<extra></extra>",
        )
    )
    fig.update_layout(
        height=360,
        margin=dict(l=40, r=20, t=30, b=40),
        xaxis_title="日期",
        yaxis_title=chart_type,
        hovermode="x unified",
    )
    if chart_type == "实时估值":
        today = now_cn().date().isoformat()
        fig.update_xaxes(
            type="date",
            range=[f"{today} 09:30:00", f"{today} 15:00:00"],
            tickformat="%H:%M",
        )
        fig.update_layout(xaxis_title="时间")
    elif len(df_chart) <= 1:
        fig.update_xaxes(type="category")
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"样本点数：{len(points)}")


def render() -> None:
    st.title("基金详情")
    watchlist_err = get_cloud_error("watchlist")
    daily_ledger_err = get_cloud_error("daily_ledger")
    if watchlist_err:
        st.warning(f"自选列表读取失败：{watchlist_err}")
    if daily_ledger_err:
        st.warning(f"历史/日结数据读取失败：{daily_ledger_err}")

    code = _pick_code_from_query_or_select()
    if not code:
        st.info("请先输入基金代码或在自选中添加基金。")
        return

    chart_type_options = ["官方净值", "实时估值", "我的收益"]
    chart_type = st.radio(
        "走势类型",
        options=chart_type_options,
        horizontal=True,
        key="fund_detail_chart_type",
    )
    if chart_type == chart_type_options[1]:
        range_value = "ALL"
        st.caption("实时估值走势不使用时间范围筛选。")
    else:
        range_value = st.radio(
            "时间范围",
            options=["1W", "1M", "3M", "6M", "1Y", "ALL"],
            horizontal=True,
            index=1,
            key="fund_detail_range",
        )

    refresh_sec = _fund_detail_refresh_sec()
    use_fragment_refresh = _fund_detail_fragment_refresh_enabled()
    live_area = st.container()
    if use_fragment_refresh:

        @st.fragment(run_every=f"{refresh_sec}s")
        def _live_fragment() -> None:
            with live_area:
                _render_live_estimate_and_chart(code, chart_type, range_value)

        _live_fragment()
    else:
        with live_area:
            _render_live_estimate_and_chart(code, chart_type, range_value)

    st.divider()
    st.subheader("估算误差分析（估算收益 vs 官方净值）")
    summary = fund_gap_summary(code, days_back=120)
    threshold = st.slider("异常阈值（绝对误差%）", min_value=0.10, max_value=2.00, value=0.30, step=0.05)
    if summary["count"] == 0:
        st.info("暂无已结算（settled）的历史对比数据。")
    else:
        latest = summary["latest"]
        abs_gap = float(latest["abs_gap_pct"])
        if abs_gap > threshold:
            st.warning(f"最近结算日误差 {abs_gap:.4f}% > 阈值 {threshold:.2f}%")
        else:
            st.success(f"最近误差 {abs_gap:.4f}% <= 阈值 {threshold:.2f}%")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("样本天数", f"{summary['count']}")
        c2.metric("平均绝对误差", f"{summary['mae_pct']:.4f}%")
        c3.metric("最大绝对误差", f"{summary['max_abs_gap_pct']:.4f}%")
        c4.metric("命中率(<=0.30%)", f"{summary['hit_rate_pct']:.1f}%")

        reasons = guess_gap_reasons(code, float(latest["abs_gap_pct"]))
        with st.expander("可能原因", expanded=True):
            for r in reasons:
                st.write("- " + r)

    st.subheader("误差历史（已结算日）")
    gap_rows = fund_gap_table(code, days_back=120)
    if not gap_rows:
        st.info("暂无误差历史。")
    else:
        df_gap = pd.DataFrame(gap_rows)
        fig_gap = go.Figure()
        fig_gap.add_trace(
            go.Scatter(
                x=df_gap["date"],
                y=df_gap["abs_gap_pct"],
                mode="lines+markers",
                name="绝对误差(%)",
                hovertemplate="日期: %{x}<br>绝对误差: %{y:.4f}%<extra></extra>",
            )
        )
        fig_gap.add_hline(y=0.30, line=dict(color="gray", dash="dot"))
        fig_gap.update_layout(height=300, margin=dict(l=40, r=20, t=30, b=40), xaxis_title="日期", yaxis_title="绝对误差(%)")
        st.plotly_chart(fig_gap, use_container_width=True)

    st.divider()
    st.subheader("当日覆盖状态（estimated_only vs settled）")
    d = st.date_input("选择日期查看覆盖状态", value=now_cn().date())
    ds = d.isoformat()
    row = get_ledger_row(ds, code)
    if not row:
        st.info("该日期在 daily_ledger 中没有记录。")
        return

    status = row.get("settle_status")
    st.write(f"**{ds} 状态：** `{status}`")
    st.json(
        {
            "estimated_nav_close": row.get("estimated_nav_close"),
            "official_nav": row.get("official_nav"),
            "estimated_pnl_close": row.get("estimated_pnl_close"),
            "official_pnl": row.get("official_pnl"),
        }
    )


render()
