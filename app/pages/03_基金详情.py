import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---- bootstrap: ensure project root in sys.path ----
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ui import (
    apply_app_style,
    configure_page,
    dataframe_height,
    degraded_notice,
    empty_state,
    estimate_method_label,
    page_header,
    safe_exception_detail,
    section_header,
    settlement_status_label,
)
from config import constants, settings
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
from services.intraday_service import record_intraday_point
from services.settlement_service import get_ledger_row
from services.trading_time import cn_market_phase, now_cn
from services.watchlist_service import watchlist_list


configure_page("基金详情", icon="🔎")
apply_app_style()
require_login(render_sidebar=False)


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
        selected = st.text_input("基金代码", value=code, placeholder="例如：510300 / 000001").strip()
    else:
        selected = st.selectbox(
            "基金代码",
            options=options,
            index=options.index(code) if code in options else 0,
            format_func=_fmt_option,
        )
    if selected and selected != code:
        try:
            st.query_params["code"] = selected
        except Exception:
            pass
    return selected


def _render_live_estimate_and_chart(code: str, chart_type: str, range_value: str) -> None:
    try:
        est = estimate_one(code)
    except Exception as exc:
        est = None
        degraded_notice(
            "实时估值暂时不可用，请稍后刷新重试。",
            safe_exception_detail(exc),
            detail_label="实时估值技术详情",
        )
    if (
        est is not None
        and est.method != constants.METHOD_FROZEN_NAV
        and float(est.est_nav or 0.0) > 0
        and float(est.confidence or 0.0) > 0
    ):
        try:
            record_intraday_point(code, estimate=est)
        except Exception:
            # Best-effort chart history must never hide a valid live estimate.
            pass

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
        st.caption(
            f"估值时间：{est.est_time if est else '—'} · "
            f"估值方式：{estimate_method_label(est.method) if est else '暂无'}"
        )

    if est:
        is_degraded = (
            est.method == constants.METHOD_FROZEN_NAV
            or float(est.confidence or 0.0) <= 0
        )
        if is_degraded:
            degraded_notice(
                "当前实时行情不可用，已降级为最近可用净值；请勿将其视为盘中实时价格。",
                est.warning or "",
                detail_label="降级原因",
            )
        elif est.warning:
            st.warning(est.warning)

    st.divider()
    section_header("走势", "图表颜色仅表示区间首尾变化，不代表投资建议。")
    chart_type_map = {
        "官方净值": CHART_OFFICIAL_NAV,
        "实时估值": CHART_REALTIME_EST,
        "我的收益": CHART_MY_PROFIT,
    }
    try:
        points = get_chart_data(code, chart_type_map[chart_type], range_value)
    except Exception as exc:
        degraded_notice(
            "走势数据暂时不可用，请稍后重试。",
            safe_exception_detail(exc),
            detail_label="走势数据技术详情",
        )
        return
    if not points:
        empty_state("暂无走势数据", "当前基金和时间范围还没有可展示的数据。")
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
    st.plotly_chart(fig, width="stretch")
    st.caption(f"样本点数：{len(points)}")


def render() -> None:
    page_header(
        "基金详情",
        "查看单只基金的盘中估值、历史走势、估值误差和日结覆盖状态。",
        eyebrow="单基金分析",
    )
    watchlist_err = get_cloud_error("watchlist")
    daily_ledger_err = get_cloud_error("daily_ledger")
    if watchlist_err:
        degraded_notice(
            "自选列表暂时不可用，基金选择项可能不完整。",
            watchlist_err,
            detail_label="自选列表技术详情",
        )
    if daily_ledger_err:
        degraded_notice(
            "历史日结数据暂时不可用，收益、误差和覆盖状态可能为空。",
            daily_ledger_err,
            detail_label="日结数据技术详情",
        )

    code = _pick_code_from_query_or_select()
    if not code:
        empty_state(
            "尚未选择基金",
            "请输入 6 位基金代码，或先在“自选与估值”中添加基金。",
        )
        return

    if st.button("刷新基金档案", help="重新获取基金名称和基础资料。"):
        try:
            profile = get_fund_profile(code, force_refresh=True)
            name = (profile.name or "").strip() or f"基金{code}"
            st.toast(f"已刷新：{code} - {name}", icon="✅")
            st.rerun()
        except Exception as e:
            degraded_notice(
                "基金档案刷新失败，当前仍保留已有资料。",
                safe_exception_detail(e),
                detail_label="基金档案技术详情",
            )

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
        range_labels = {
            "近 1 周": "1W",
            "近 1 个月": "1M",
            "近 3 个月": "3M",
            "近 6 个月": "6M",
            "近 1 年": "1Y",
            "全部": "ALL",
        }
        selected_range = st.radio(
            "时间范围",
            options=list(range_labels),
            horizontal=True,
            index=1,
            key="fund_detail_range_label",
        )
        range_value = range_labels[selected_range]

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
    section_header(
        "估值误差分析",
        "比较收盘估算收益与官方净值收益，仅统计已经完成官方结算的日期。",
    )
    threshold = st.slider("异常阈值（绝对误差%）", min_value=0.10, max_value=2.00, value=0.30, step=0.05)
    summary = fund_gap_summary(code, days_back=120, hit_threshold_pct=threshold)
    if summary["count"] == 0:
        empty_state("暂无已结算的对比数据", "完成至少一个交易日的官方净值结算后即可查看。")
    else:
        latest = summary["latest"]
        abs_gap = float(latest["abs_gap_pct"])
        if abs_gap > threshold:
            st.warning(f"最近结算日误差 {abs_gap:.4f}% 高于阈值 {threshold:.2f}%。")
        else:
            st.success(f"最近结算日误差 {abs_gap:.4f}% 不高于阈值 {threshold:.2f}%。")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("样本天数", f"{summary['count']}")
        c2.metric("平均绝对误差", f"{summary['mae_pct']:.4f}%")
        c3.metric("最大绝对误差", f"{summary['max_abs_gap_pct']:.4f}%")
        c4.metric(f"阈值内比例（≤{threshold:.2f}%）", f"{summary['hit_rate_pct']:.1f}%")

        reasons = guess_gap_reasons(code, float(latest["abs_gap_pct"]))
        with st.expander("可能原因", expanded=True):
            for r in reasons:
                st.write("- " + r)

    st.subheader("误差历史")
    gap_rows = fund_gap_table(code, days_back=120)
    if not gap_rows:
        empty_state("暂无误差历史", "完成官方净值结算后，这里会显示历史误差走势。")
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
        fig_gap.add_hline(y=threshold, line=dict(color="gray", dash="dot"))
        fig_gap.update_layout(height=300, margin=dict(l=40, r=20, t=30, b=40), xaxis_title="日期", yaxis_title="绝对误差(%)")
        st.plotly_chart(fig_gap, width="stretch")
        with st.expander("查看误差明细", expanded=False):
            gap_show = df_gap.rename(
                columns={
                    "date": "日期",
                    "code": "基金代码",
                    "estimated_change_pct": "估算收益率（%）",
                    "official_change_pct": "官方收益率（%）",
                    "gap_pct": "误差率（%）",
                    "abs_gap_pct": "绝对误差率（%）",
                }
            )
            st.dataframe(
                gap_show,
                width="stretch",
                hide_index=True,
                height=dataframe_height(len(gap_show), max_rows=10),
            )

    st.divider()
    section_header("日结覆盖状态", "检查所选日期目前只有收盘估算，还是已经按官方净值完成结算。")
    d = st.date_input(
        "选择日期查看覆盖状态",
        value=now_cn().date(),
        max_value=now_cn().date(),
    )
    ds = d.isoformat()
    row = get_ledger_row(ds, code)
    if not row:
        empty_state("所选日期没有日结记录", "可到“日结台账”生成收盘估算或完成结算。")
        return

    status = row.get("settle_status")
    status_label = settlement_status_label(status)
    if status == constants.SETTLE_SETTLED:
        st.success(f"{ds}：{status_label}")
    else:
        st.info(f"{ds}：{status_label}")

    def _metric_value(value: object, digits: int = 6, suffix: str = "") -> str:
        try:
            return f"{float(value):.{digits}f}{suffix}"
        except (TypeError, ValueError):
            return "—"

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("收盘估算净值", _metric_value(row.get("estimated_nav_close")))
    s2.metric("官方净值", _metric_value(row.get("official_nav")))
    s3.metric("收盘估算收益", _metric_value(row.get("estimated_pnl_close"), 2, " 元"))
    s4.metric("官方收益", _metric_value(row.get("official_pnl"), 2, " 元"))


render()
