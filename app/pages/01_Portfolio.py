import sys
from pathlib import Path

# ---- bootstrap: ensure project root in sys.path ----
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from datetime import date

import pandas as pd
import plotly.graph_objects as go

from services.portfolio_service import portfolio_realtime_view_as_of
from services.edit_bridge_service import apply_position_edit
from services.snapshot_service import build_positions_as_of
from services.accuracy_service import portfolio_gap_summary, portfolio_gap_table


st.set_page_config(page_title="Portfolio", layout="wide")


def render_portfolio():
    st.title("持仓 - 实时估值（按流水回放快照）")

    d = st.date_input("as_of 日期", value=date.today())
    date_str = d.isoformat()

    # 组合视图（快照口径）
    view = portfolio_realtime_view_as_of(date_str)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("总成本", f"{view['total_cost']:.2f}")
    c2.metric("预估市值", f"{view['total_est_value']:.2f}")
    c3.metric("预估盈亏", f"{view['total_est_pnl']:.2f}")
    c4.metric("预估盈亏率", f"{view['total_est_pnl_pct']:.2f}%")
    st.caption(f"估值覆盖率：{view['realtime_coverage_value_pct']:.2f}%")

    st.subheader("组合口径：估算收盘 vs 官方净值")

    ps = portfolio_gap_summary(days_back=120)
    if ps["count"] == 0:
        st.info("暂无组合层面的 settled 对比数据。")
    else:
        latest = ps["latest"]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("样本天数", f'{ps["count"]}')
        c2.metric("平均绝对误差", f'{ps["mae_pct"]:.4f}%')
        c3.metric("最大绝对误差", f'{ps["max_abs_gap_pct"]:.4f}%')
        c4.metric("命中率(≤0.30%)", f'{ps["hit_rate_pct"]:.1f}%')

        st.markdown(
            f"""
    **最新已结算日：{latest["date"]}**  
    - 估算收盘组合价值：`{latest["est_value"]:.2f}`  
    - 官方口径组合价值：`{latest["off_value"]:.2f}`  
    - 误差：`{latest["gap"]:+.2f}`（`{latest["gap_pct"]:+.4f}%`）
    """
        )

    st.subheader("组合误差历史（已结算日）")


    rows = portfolio_gap_table(days_back=120)
    threshold_p = st.slider("组合异常阈值（绝对误差%）", min_value=0.10, max_value=2.00, value=0.30, step=0.05)

    if not rows:
        st.info("暂无组合误差历史（需要 settled 数据）。")
    else:
        dfp = pd.DataFrame(rows)

        abs_gap = float(latest["abs_gap_pct"])
        if abs_gap > threshold_p:
            st.warning(f"⚠️ 组合最近误差 {abs_gap:.4f}% > 阈值 {threshold_p:.2f}%：盘中组合收益可能偏离官方口径。")
        else:
            st.success(f"✅ 组合最近误差 {abs_gap:.4f}% ≤ 阈值 {threshold_p:.2f}%：盘中组合口径较稳定。")

        total_n = len(dfp)

        if total_n <= 1:
            show_n = total_n
        else:
            min_n = 2
            max_n = min(60, total_n)
            default_n = min(20, max_n)

            show_n = st.slider(
                "展示最近 N 个已结算日（组合）",
                min_value=min_n,
                max_value=max_n,
                value=default_n,
                step=1,
            )

        dfp_show = dfp.tail(show_n).copy()

        dfp_show = dfp.tail(show_n).copy()

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=dfp_show["date"],
                y=dfp_show["abs_gap_pct"],
                mode="lines+markers",
                name="组合绝对误差(%)",
                hovertemplate="日期: %{x}<br>绝对误差: %{y:.4f}%<extra></extra>",
            )
        )
        fig.add_hline(
            y=0.30,
            line=dict(color="gray", dash="dot"),
            annotation_text="0.30% 阈值",
            annotation_position="top left",
        )
        fig.update_layout(
            height=300,
            margin=dict(l=40, r=20, t=30, b=40),
            xaxis_title="日期",
            yaxis_title="绝对误差(%)",
        )
        st.plotly_chart(fig, use_container_width=True)

        st.dataframe(
            dfp_show[["date", "estimated_value_close", "official_value", "gap", "gap_pct", "abs_gap_pct"]],
            width="stretch",
            hide_index=True,
        )
        st.caption("说明：组合误差是按 shares_end×净值 聚合得到的近似总价值差异。")



    st.subheader("明细")
    st.dataframe(view["positions"], width="stretch", hide_index=True)

    st.divider()
    st.subheader("编辑持仓（生成流水）")

    # === 关键：从快照拿默认值，避免误点保存写出脏流水 ===
    snaps = build_positions_as_of(date_str)
    snap_map = {s.code: s for s in snaps}

    # 给一个默认选项，方便你第一次用
    code_opts = sorted(set(snap_map.keys()) | set(["510300"]))
    code = st.selectbox("基金代码", options=code_opts)

    cur = snap_map.get(code)
    cur_shares = float(cur.shares_end) if cur else 0.0
    cur_cost = float(cur.avg_cost_nav_end) if cur else 0.0
    cur_realized = float(cur.realized_pnl_end) if cur else 0.0

    colA, colB, colC = st.columns(3)
    with colA:
        shares_end = st.number_input(
            "当日结束份额 shares_end",
            min_value=0.0,
            value=cur_shares,
            step=10.0
        )
    with colB:
        avg_cost = st.number_input(
            "当日结束成本净值 avg_cost_nav_end",
            min_value=0.0,
            value=cur_cost,
            step=0.01,
            format="%.4f"
        )
    with colC:
        realized = st.number_input(
            "当日结束已实现收益 realized_pnl_end",
            value=cur_realized,
            step=1.0,
            format="%.4f"
        )

    note = st.text_input("备注（可选）", value="UI编辑")

    if st.button("保存编辑（写入流水）", type="primary"):
        # 防呆：原本有持仓但你改成 0，强制你在备注里写“确认清仓”
        if cur_shares > 0 and float(shares_end) == 0.0 and "确认清仓" not in (note or ""):
            st.error("检测到你把持仓从非 0 改为 0（可能是误操作）。如确实要清仓，请在备注中包含：确认清仓，然后再点保存。")
            st.stop()

        apply_position_edit(
            effective_date=date_str,
            code=code,
            shares_end=float(shares_end),
            avg_cost_nav_end=float(avg_cost),
            realized_pnl_end=float(realized),
            note=note,
        )
        st.success("已写入流水（adjustments.json），并可回放快照。")
        st.rerun()


render_portfolio()
