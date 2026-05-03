import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

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


st.set_page_config(page_title="Portfolio Analysis", layout="wide")
require_login()


def _fund_name_safe(code: str) -> str:
    try:
        profile = get_fund_profile(str(code))
        return (profile.name or "").strip()
    except Exception:
        return ""


def _load_view(date_str: str) -> dict:
    cache_key = f"_analysis_portfolio_view_{date_str}"
    cached = st.session_state.get(cache_key)
    if isinstance(cached, dict):
        return cached
    view = portfolio_realtime_view_as_of(date_str)
    st.session_state[cache_key] = view
    return view


def _render_curve() -> None:
    st.subheader("组合曲线")
    days = st.slider("组合曲线天数", min_value=30, max_value=365, value=180, step=30)
    curve_rows = portfolio_nav_curve(days=days)
    if not curve_rows:
        st.info("暂无组合历史曲线数据。请先到“日结”页生成日结数据。")
        return

    df_curve = pd.DataFrame(curve_rows)
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df_curve["date"],
            y=df_curve["portfolio_index"],
            mode="lines+markers",
            name="组合规模指数",
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
    st.plotly_chart(fig, use_container_width=True)

    latest = df_curve.iloc[-1]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("最新指数", f"{float(latest['portfolio_index']):.2f}")
    c2.metric("组合市值", f"{float(latest['total_value']):.2f}")
    c3.metric("累计盈亏", f"{float(latest['total_pnl']):.2f}")
    c4.metric("累计收益率", f"{float(latest['total_pnl_pct']):.2f}%")
    st.caption("说明：组合规模指数按每日组合市值计算，发生大额申赎或调仓时会受到资金流影响。")


def _render_attribution(date_str: str) -> None:
    st.subheader("收益归因")
    view = _load_view(date_str)
    rows = portfolio_attribution_rows(view)
    if not rows:
        st.info("暂无持仓归因数据。")
        return

    df_attr = pd.DataFrame(rows)
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
    st.dataframe(df_attr[[c for c in show_cols if c in df_attr.columns]], width="stretch", hide_index=True)


def _render_targets(date_str: str) -> None:
    st.subheader("目标仓位")
    view = _load_view(date_str)
    targets = load_target_allocations()
    target_rows = target_allocation_rows(view, targets)
    current_codes = [str(r.get("code", "")) for r in target_rows if str(r.get("code", "")).strip()]
    if not current_codes:
        st.info("暂无持仓，无法设置目标仓位。")
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
            "当前占比(%)": st.column_config.NumberColumn(format="%.2f"),
            "目标占比(%)": st.column_config.NumberColumn(min_value=0.0, max_value=100.0, step=1.0, format="%.2f"),
        },
        key="target_allocation_editor",
    )
    c_save, c_clear = st.columns(2)
    with c_save:
        if st.button("保存目标仓位", width="stretch"):
            payload = {}
            for row in edited.to_dict("records"):
                payload[str(row.get("基金代码", "")).strip()] = float(row.get("目标占比(%)", 0.0) or 0.0)
            save_target_allocations(payload)
            st.success("目标仓位已保存。")
            st.session_state.pop(f"_analysis_portfolio_view_{date_str}", None)
            st.rerun()
    with c_clear:
        if st.button("清空目标仓位", width="stretch"):
            save_target_allocations({})
            st.success("目标仓位已清空。")
            st.rerun()

    saved_rows = target_allocation_rows(view, load_target_allocations())
    if not saved_rows:
        return
    df_target = pd.DataFrame(saved_rows)
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
    st.dataframe(
        df_target[["基金代码", "基金名称", "当前占比(%)", "目标占比(%)", "偏离(%)", "偏离金额", "预估市值"]],
        width="stretch",
        hide_index=True,
    )


def _render_health() -> None:
    st.subheader("数据检查")
    days_back = st.slider("检查最近 N 天日结", min_value=3, max_value=30, value=7, step=1)
    issues = portfolio_health_check(days_back=days_back)
    df_issues = pd.DataFrame(issues)
    if df_issues.empty:
        st.info("暂无检查结果。")
        return
    level_order = {"error": 0, "warning": 1, "info": 2, "success": 3}
    df_issues["_order"] = df_issues["level"].map(level_order).fillna(9)
    df_issues = df_issues.sort_values(["_order", "scope"], kind="stable").drop(columns=["_order"])
    df_issues = df_issues.rename(columns={"level": "级别", "scope": "范围", "message": "问题", "suggestion": "建议"})
    st.dataframe(df_issues, width="stretch", hide_index=True)


def render() -> None:
    st.title("组合分析")
    daily_ledger_err = get_cloud_error("daily_ledger")
    adjustments_err = get_cloud_error("adjustments")
    if daily_ledger_err:
        st.warning(f"日结数据读取失败，部分历史分析可能为空：{daily_ledger_err}")
    if adjustments_err:
        st.warning(f"持仓流水读取失败，数据检查可能不完整：{adjustments_err}")

    d = st.date_input("分析日期", value=now_cn().date())
    date_str = d.isoformat()
    tabs = st.tabs(["组合曲线", "收益归因", "目标仓位", "数据检查"])
    with tabs[0]:
        _render_curve()
    with tabs[1]:
        _render_attribution(date_str)
    with tabs[2]:
        _render_targets(date_str)
    with tabs[3]:
        _render_health()


render()
