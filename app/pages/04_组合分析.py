import sys
import math
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.ui import (
    apply_app_style,
    configure_page,
    danger_container,
    dataframe_height,
    degraded_notice,
    empty_state,
    page_header,
    section_header,
)
from services.auth_guard import require_login
from services.cloud_status_service import get_cloud_error
from services.fund_service import get_fund_profile
from services.portfolio_analysis_service import (
    load_target_allocations,
    portfolio_attribution_rows,
    portfolio_health_check,
    portfolio_nav_curve,
    save_target_allocations,
    target_allocation_rows,
)
from services.portfolio_service import portfolio_realtime_view_as_of
from services.trading_time import now_cn


configure_page("组合分析", icon="📈")
apply_app_style()
CURRENT_USER_ID = require_login(render_sidebar=False)

UP_COLOR = "#d92d20"
DOWN_COLOR = "#039855"
NEUTRAL_COLOR = "#344054"


def _signed_color(value: object) -> str:
    try:
        num = float(value or 0.0)
    except Exception:
        num = 0.0
    if num > 0:
        return f"color: {UP_COLOR}"
    if num < 0:
        return f"color: {DOWN_COLOR}"
    return f"color: {NEUTRAL_COLOR}"


def _fund_name_safe(code: str) -> str:
    code_s = str(code or "").strip()
    if not code_s:
        return ""
    try:
        profile = get_fund_profile(code_s)
        return (profile.name or "").strip() or f"基金{code_s}"
    except Exception:
        return f"基金{code_s}"


def _load_view(date_str: str) -> dict:
    cache_key = f"_analysis_portfolio_view_{CURRENT_USER_ID}_{date_str}"
    now_ts = time.time()
    ttl_sec = 20.0 if date_str == now_cn().date().isoformat() else 300.0
    cached = st.session_state.get(cache_key)
    if (
        isinstance(cached, dict)
        and isinstance(cached.get("view"), dict)
        and (now_ts - float(cached.get("ts", 0.0))) <= ttl_sec
    ):
        return cached["view"]
    view = portfolio_realtime_view_as_of(date_str)
    st.session_state[cache_key] = {"ts": now_ts, "view": view}
    return view


def _clear_view_cache(date_str: str) -> None:
    st.session_state.pop(f"_analysis_portfolio_view_{CURRENT_USER_ID}_{date_str}", None)


def _render_curve() -> None:
    section_header(
        "组合曲线",
        "以首个可用日为 100 展示组合规模变化；大额申赎和调仓也会影响曲线。",
    )
    days = st.slider("组合曲线天数", min_value=30, max_value=365, value=180, step=30)
    curve_rows = portfolio_nav_curve(days=days)
    if not curve_rows:
        empty_state("暂无组合历史曲线", "先到“日结台账”生成日结数据，再回来查看。")
        return

    df_curve = pd.DataFrame(curve_rows)
    line_color = NEUTRAL_COLOR
    try:
        first_index = float(df_curve["portfolio_index"].iloc[0])
        latest_index = float(df_curve["portfolio_index"].iloc[-1])
        if latest_index > first_index:
            line_color = UP_COLOR
        elif latest_index < first_index:
            line_color = DOWN_COLOR
    except Exception:
        pass
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df_curve["date"],
            y=df_curve["portfolio_index"],
            mode="lines+markers",
            name="组合规模指数",
            line=dict(color=line_color, width=2),
            hovertemplate="日期: %{x}<br>指数: %{y:.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=340,
        margin=dict(l=40, r=20, t=30, b=40),
        xaxis_title="日期",
        yaxis_title="组合规模指数（首日=100）",
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")

    latest = df_curve.iloc[-1]
    first = df_curve.iloc[0]
    index_delta = float(latest["portfolio_index"]) - float(first["portfolio_index"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("最新指数", f"{float(latest['portfolio_index']):.2f}", delta=f"{index_delta:+.2f}")
    c2.metric("组合市值", f"{float(latest['total_value']):.2f} 元")
    c3.metric("累计盈亏", f"{float(latest['total_pnl']):.2f} 元")
    c4.metric("累计收益率", f"{float(latest['total_pnl_pct']):.2f}%")
    st.caption("说明：组合规模指数按每日组合市值计算，发生大额申赎或调仓时会受到资金流影响。")


def _render_attribution(date_str: str) -> None:
    section_header("收益归因", "拆解今天每只基金对组合预估收益的贡献。")
    if date_str != now_cn().date().isoformat():
        st.info("历史日期暂不提供单日收益归因，请选择今天查看实时归因。")
        return
    view = _load_view(date_str)
    rows = portfolio_attribution_rows(view)
    if not rows:
        empty_state("暂无收益归因数据", "所选日期没有可用于归因的持仓或估值。")
        return

    df_attr = pd.DataFrame(rows)
    total_today = float(pd.to_numeric(df_attr["today_pnl"], errors="coerce").fillna(0.0).sum())
    positive_count = int((pd.to_numeric(df_attr["today_pnl"], errors="coerce").fillna(0.0) > 0).sum())
    negative_count = int((pd.to_numeric(df_attr["today_pnl"], errors="coerce").fillna(0.0) < 0).sum())
    top_driver = rows[0] if rows else {}
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("今日预计收益", f"{total_today:.2f} 元")
    a2.metric("正贡献/负贡献", f"{positive_count}/{negative_count}")
    a3.metric("最大驱动", str(top_driver.get("code", "-")))
    a4.metric("驱动收益", f"{float(top_driver.get('today_pnl', 0.0) or 0.0):.2f} 元")

    df_attr["fund_name"] = df_attr["code"].apply(_fund_name_safe)
    df_attr = df_attr.rename(
        columns={
            "code": "基金代码",
            "fund_name": "基金名称",
            "weight_pct": "仓位占比(%)",
            "est_value": "预估市值",
            "today_pnl": "今日预计收益",
            "today_contribution_pct": "今日贡献占比(%)",
            "total_pnl": "累计收益",
            "total_pnl_pct": "累计收益率(%)",
            "confidence": "置信度",
            "warning": "提示",
        }
    )
    show_cols = [
        "基金代码",
        "基金名称",
        "仓位占比(%)",
        "今日预计收益",
        "今日贡献占比(%)",
        "累计收益",
        "累计收益率(%)",
        "置信度",
        "提示",
    ]
    for col in ["仓位占比(%)", "今日预计收益", "今日贡献占比(%)", "累计收益", "累计收益率(%)", "置信度"]:
        if col in df_attr.columns:
            df_attr[col] = pd.to_numeric(df_attr[col], errors="coerce").round(2)
    df_show = df_attr[[c for c in show_cols if c in df_attr.columns]]
    styler = df_show.style
    for col in ["今日预计收益", "累计收益", "累计收益率(%)"]:
        if col in df_show.columns:
            styler = styler.map(_signed_color, subset=[col])
    st.dataframe(
        styler,
        width="stretch",
        hide_index=True,
        height=dataframe_height(len(df_show), max_rows=12),
        column_config={
            "仓位占比(%)": st.column_config.NumberColumn("仓位占比（%）", format="%.2f"),
            "今日预计收益": st.column_config.NumberColumn("今日预计收益（元）", format="%.2f"),
            "今日贡献占比(%)": st.column_config.NumberColumn("今日贡献占比（%）", format="%.2f"),
            "累计收益": st.column_config.NumberColumn("累计收益（元）", format="%.2f"),
            "累计收益率(%)": st.column_config.NumberColumn("累计收益率（%）", format="%.2f"),
            "置信度": st.column_config.NumberColumn(format="%.2f"),
        },
    )


def _render_targets(date_str: str) -> None:
    section_header("目标仓位", "设置各基金的目标占比，并查看偏离金额与再平衡建议。")
    view = _load_view(date_str)
    targets = load_target_allocations()
    if not isinstance(targets, dict):
        targets = {}
    target_rows = target_allocation_rows(view, targets)
    current_codes = [str(r.get("code", "")) for r in target_rows if str(r.get("code", "")).strip()]
    if not current_codes:
        empty_state("暂无持仓", "先到“持仓管理”录入持仓，之后才能设置目标仓位。")
        return

    editor_rows = []
    for row in target_rows:
        code = str(row.get("code", "")).strip()
        editor_rows.append(
            {
                "基金代码": code,
                "基金名称": _fund_name_safe(code),
                "当前占比(%)": round(float(row.get("current_pct", 0.0) or 0.0), 2),
                "目标占比(%)": round(float(row.get("target_pct", 0.0) or 0.0), 2),
            }
        )
    st.caption("填写每只基金目标占比后保存，下方会计算当前仓位和目标仓位的偏离金额。")
    edited = st.data_editor(
        pd.DataFrame(editor_rows),
        width="stretch",
        hide_index=True,
        disabled=["基金代码", "基金名称", "当前占比(%)"],
        column_config={
            "当前占比(%)": st.column_config.NumberColumn("当前占比（%）", format="%.2f"),
            "目标占比(%)": st.column_config.NumberColumn(
                "目标占比（%）",
                min_value=0.0,
                max_value=100.0,
                step=1.0,
                format="%.2f",
            ),
        },
        key="target_allocation_editor",
        height=dataframe_height(len(editor_rows), max_rows=12),
    )
    c_save, c_clear = st.columns(2)
    with c_save:
        if st.button("保存目标仓位", type="primary", width="stretch"):
            payload = {}
            for row in edited.to_dict("records"):
                payload[str(row.get("基金代码", "")).strip()] = float(row.get("目标占比(%)", 0.0) or 0.0)
            save_target_allocations(payload)
            st.success("目标仓位已保存。")
            _clear_view_cache(date_str)
            st.rerun()
    with c_clear:
        has_saved_targets = any(float(value or 0.0) > 0 for value in targets.values())
        if has_saved_targets:
            confirm_clear_targets = st.checkbox(
                "我确认清空全部目标占比",
                value=False,
            )
            with danger_container("clear_targets"):
                if st.button(
                    "清空全部目标仓位",
                    type="primary",
                    width="stretch",
                    disabled=not confirm_clear_targets,
                ):
                    save_target_allocations({})
                    st.success("目标仓位已清空。")
                    st.rerun()
        else:
            st.caption("尚无已保存的目标仓位，无需清空。")

    saved_targets = load_target_allocations()
    if not isinstance(saved_targets, dict):
        saved_targets = {}
    saved_target_sum = sum(max(0.0, float(value or 0.0)) for value in saved_targets.values())
    if saved_target_sum <= 0:
        st.info("尚未设置目标仓位。请先填写目标占比并保存，再生成偏离和再平衡建议。")
        return

    saved_rows = target_allocation_rows(view, saved_targets)
    if not saved_rows:
        return
    df_target = pd.DataFrame(saved_rows)
    target_pct_series = pd.to_numeric(df_target.get("target_pct", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    deviation_series = pd.to_numeric(df_target.get("deviation_amount", pd.Series(dtype=float)), errors="coerce").fillna(0.0).abs()
    target_sum = float(target_pct_series.sum())
    max_deviation = float(deviation_series.max()) if not deviation_series.empty else 0.0
    if not math.isfinite(max_deviation):
        max_deviation = 0.0
    need_adjust_count = int((deviation_series > 1.0).sum())
    t1, t2, t3 = st.columns(3)
    t1.metric("目标仓位合计", f"{target_sum:.2f}%")
    t2.metric("最大偏离金额", f"{max_deviation:.2f} 元")
    t3.metric("需关注标的", need_adjust_count)
    if target_sum > 0 and abs(target_sum - 100.0) > 0.01:
        st.warning(f"目标仓位合计为 {target_sum:.2f}%，建议按组合口径调整到 100%。")

    df_target["fund_name"] = df_target["code"].apply(_fund_name_safe)
    df_target = df_target.rename(
        columns={
            "code": "基金代码",
            "fund_name": "基金名称",
            "current_pct": "当前占比(%)",
            "target_pct": "目标占比(%)",
            "deviation_pct": "偏离(%)",
            "deviation_amount": "偏离金额",
            "est_value": "预估市值",
        }
    )
    for col in ["当前占比(%)", "目标占比(%)", "偏离(%)", "偏离金额", "预估市值"]:
        if col in df_target.columns:
            df_target[col] = pd.to_numeric(df_target[col], errors="coerce").round(2)
    target_show = df_target[["基金代码", "基金名称", "当前占比(%)", "目标占比(%)", "偏离(%)", "偏离金额", "预估市值"]]
    target_styler = target_show.style
    for col in ["偏离(%)", "偏离金额"]:
        target_styler = target_styler.map(_signed_color, subset=[col])
    st.dataframe(
        target_styler,
        width="stretch",
        hide_index=True,
        height=dataframe_height(len(target_show), max_rows=12),
        column_config={
            "当前占比(%)": st.column_config.NumberColumn("当前占比（%）", format="%.2f"),
            "目标占比(%)": st.column_config.NumberColumn("目标占比（%）", format="%.2f"),
            "偏离(%)": st.column_config.NumberColumn("偏离（%）", format="%.2f"),
            "偏离金额": st.column_config.NumberColumn("偏离金额（元）", format="%.2f"),
            "预估市值": st.column_config.NumberColumn("预估市值（元）", format="%.2f"),
        },
    )

    st.subheader("再平衡建议")
    threshold_amount = st.number_input(
        "最小建议金额",
        min_value=0.0,
        value=100.0,
        step=100.0,
        format="%.2f",
        help="偏离金额绝对值小于该值的标的不生成买卖建议。",
    )
    advice_rows = []
    for row in saved_rows:
        code = str(row.get("code", "") or "").strip()
        deviation_amount = float(row.get("deviation_amount", 0.0) or 0.0)
        if not code or abs(deviation_amount) < threshold_amount:
            continue
        advice_rows.append(
            {
                "基金代码": code,
                "基金名称": _fund_name_safe(code),
                "建议操作": "卖出" if deviation_amount > 0 else "买入",
                "建议金额": round(abs(deviation_amount), 2),
                "当前占比(%)": round(float(row.get("current_pct", 0.0) or 0.0), 2),
                "目标占比(%)": round(float(row.get("target_pct", 0.0) or 0.0), 2),
                "偏离(%)": round(float(row.get("deviation_pct", 0.0) or 0.0), 2),
            }
        )
    if advice_rows:
        advice_df = pd.DataFrame(advice_rows)
        st.dataframe(
            advice_df,
            width="stretch",
            hide_index=True,
            column_config={
                "建议金额": st.column_config.NumberColumn("建议金额（元）", format="%.2f"),
                "当前占比(%)": st.column_config.NumberColumn("当前占比（%）", format="%.2f"),
                "目标占比(%)": st.column_config.NumberColumn("目标占比（%）", format="%.2f"),
                "偏离(%)": st.column_config.NumberColumn("偏离（%）", format="%.2f"),
            },
            height=dataframe_height(len(advice_df), max_rows=12),
        )
        st.download_button(
            "下载再平衡建议 CSV",
            data=advice_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"rebalance_advice_{date_str}.csv",
            mime="text/csv",
            width="stretch",
        )
    else:
        empty_state("暂无再平衡建议", "当前没有超过最小建议金额的仓位偏离。")


def _render_health() -> None:
    section_header("数据检查", "检查近期持仓流水、日结台账和目标仓位中的常见数据问题。")
    days_back = st.slider("检查最近 N 天日结", min_value=3, max_value=30, value=7, step=1)
    issues = portfolio_health_check(days_back=days_back)
    df_issues = pd.DataFrame(issues)
    if df_issues.empty:
        empty_state("暂无检查结果", "当前没有可检查的数据。")
        return
    level_counts = df_issues["level"].value_counts()
    h1, h2, h3, h4 = st.columns(4)
    h1.metric("错误", int(level_counts.get("error", 0)))
    h2.metric("警告", int(level_counts.get("warning", 0)))
    h3.metric("提示", int(level_counts.get("info", 0)))
    h4.metric("通过", int(level_counts.get("success", 0)))
    level_order = {"error": 0, "warning": 1, "info": 2, "success": 3}
    df_issues["_order"] = df_issues["level"].map(level_order).fillna(9)
    df_issues = df_issues.sort_values(["_order", "scope"], kind="stable").drop(columns=["_order"])
    level_value_by_label = {
        "全部": "全部",
        "错误": "error",
        "警告": "warning",
        "提示": "info",
        "通过": "success",
    }
    scope_options = ["全部"] + sorted(str(x) for x in df_issues["scope"].dropna().unique())
    c_level, c_scope = st.columns(2)
    with c_level:
        level_label = st.selectbox("按级别筛选", options=list(level_value_by_label))
        level_filter = level_value_by_label[level_label]
    with c_scope:
        scope_filter = st.selectbox("按范围筛选", options=scope_options)
    filtered = df_issues.copy()
    if level_filter != "全部":
        filtered = filtered[filtered["level"] == level_filter]
    if scope_filter != "全部":
        filtered = filtered[filtered["scope"] == scope_filter]
    df_issues = df_issues.rename(columns={"level": "级别", "scope": "范围", "message": "问题", "suggestion": "建议"})
    filtered = filtered.rename(columns={"level": "级别", "scope": "范围", "message": "问题", "suggestion": "建议"})
    level_labels = {"error": "错误", "warning": "警告", "info": "提示", "success": "通过"}
    if "级别" in filtered.columns:
        filtered["级别"] = filtered["级别"].map(level_labels).fillna(filtered["级别"])
    st.caption(f"当前显示 {len(filtered)} / {len(df_issues)} 条检查结果。")
    if filtered.empty:
        empty_state("没有符合条件的检查结果", "调整级别或范围筛选后再试。")
        return
    st.dataframe(
        filtered,
        width="stretch",
        hide_index=True,
        height=dataframe_height(len(filtered), max_rows=12),
    )


def render() -> None:
    page_header(
        "组合分析",
        "从组合曲线、收益归因、目标仓位和数据质量四个角度理解投资组合。",
        eyebrow="投资组合",
    )
    daily_ledger_err = get_cloud_error("daily_ledger")
    adjustments_err = get_cloud_error("adjustments")
    if daily_ledger_err:
        degraded_notice(
            "日结数据暂时不可用，部分历史分析可能为空。",
            daily_ledger_err,
            detail_label="日结数据技术详情",
        )
    if adjustments_err:
        degraded_notice(
            "持仓流水暂时不可用，数据检查结果可能不完整。",
            adjustments_err,
            detail_label="持仓流水技术详情",
        )

    section = st.radio(
        "分析视图",
        ["组合曲线", "收益归因", "目标仓位", "数据检查"],
        horizontal=True,
    )
    date_str = now_cn().date().isoformat()
    if section in {"收益归因", "目标仓位"}:
        date_col, refresh_col = st.columns([3, 1])
        with date_col:
            d = st.date_input("分析日期", value=now_cn().date(), max_value=now_cn().date())
            date_str = d.isoformat()
        with refresh_col:
            if st.button("刷新分析数据", width="stretch"):
                _clear_view_cache(date_str)
                st.rerun()

    if section == "组合曲线":
        _render_curve()
    elif section == "收益归因":
        _render_attribution(date_str)
    elif section == "目标仓位":
        _render_targets(date_str)
    else:
        _render_health()


render()
