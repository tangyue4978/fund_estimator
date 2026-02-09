import sys
from pathlib import Path

# ---- bootstrap: ensure project root in sys.path ----
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from datetime import date, timedelta
try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # pragma: no cover
    st_autorefresh = None

import pandas as pd
import plotly.graph_objects as go

from services.portfolio_service import portfolio_realtime_view_as_of
from services.edit_bridge_service import apply_position_edit
from services.snapshot_service import build_positions_as_of
from services.watchlist_service import watchlist_list
from services.accuracy_service import portfolio_gap_summary, portfolio_gap_table
from services.fund_service import get_fund_profile
from services.estimation_service import estimate_one
from services.history_service import get_fund_cumulative_pnl_on
from services import adjustment_service
from storage import paths
from services.auth_guard import require_login
from services.trading_time import now_cn
from storage.json_store import load_json, update_json
from config import settings


st.set_page_config(page_title="Portfolio", layout="wide")
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


# auto refresh (Portfolio)
_portfolio_auto_on = bool(getattr(settings, "PORTFOLIO_AUTO_REFRESH_ENABLED", True))
_portfolio_refresh_raw = getattr(settings, "PORTFOLIO_AUTO_REFRESH_SEC", 30)
_portfolio_refresh_sec = int(30 if _portfolio_refresh_raw is None else _portfolio_refresh_raw)
if _portfolio_auto_on and _portfolio_refresh_sec > 0:
    _apply_silent_autorefresh_style()
    if st_autorefresh is not None:
        st_autorefresh(interval=int(_portfolio_refresh_sec) * 1000, key="portfolio_autorefresh")
    elif hasattr(st, "autorefresh"):
        st.autorefresh(interval=int(_portfolio_refresh_sec) * 1000, key="portfolio_autorefresh")


def _input_amount_path() -> str:
    return str(paths.user_data_dir() / "position_input_amounts.json")


def _load_input_amount_map(date_str: str) -> dict:
    data = load_json(_input_amount_path(), fallback={"items": {}})
    items = data.get("items", {}) if isinstance(data, dict) else {}
    if not isinstance(items, dict):
        return {}

    # Roll-forward: for each code, use the latest recorded amount on or before date_str.
    out: dict = {}
    for d in sorted(items.keys()):
        if str(d) > str(date_str):
            continue
        by_date = items.get(d, {})
        if not isinstance(by_date, dict):
            continue
        for code, amount in by_date.items():
            try:
                out[str(code)] = float(amount)
            except Exception:
                continue
    return out


def _save_input_amount(date_str: str, code: str, amount: float) -> None:
    def updater(data: dict):
        items = data.get("items", {})
        if not isinstance(items, dict):
            items = {}
        by_date = items.get(date_str, {})
        if not isinstance(by_date, dict):
            by_date = {}
        by_date[code] = float(amount)
        items[date_str] = by_date
        data["items"] = items
        return data

    update_json(_input_amount_path(), updater)


def _delete_input_amount_for_code(code: str) -> None:
    code = (code or "").strip()
    if not code:
        return

    def updater(data: dict):
        items = data.get("items", {})
        if not isinstance(items, dict):
            items = {}
        for d, by_date in list(items.items()):
            if not isinstance(by_date, dict):
                continue
            if code in by_date:
                by_date.pop(code, None)
            items[d] = by_date
        data["items"] = items
        return data

    update_json(_input_amount_path(), updater)


def _remove_adjustments_by_code_safe(code: str) -> int:
    code = (code or "").strip()
    if not code:
        return 0
    fn = getattr(adjustment_service, "remove_adjustments_by_code", None)
    if callable(fn):
        return int(fn(code) or 0)

    # fallback for older runtime/module cache
    removed = {"count": 0}
    p = paths.file_adjustments()

    def updater(data: dict):
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        new_items = []
        cnt = 0
        for it in items:
            if str(it.get("code", "")).strip() == code:
                cnt += 1
            else:
                new_items.append(it)
        removed["count"] = cnt
        data["items"] = new_items
        return data

    update_json(p, updater)
    return removed["count"]


def render_portfolio():
    st.title("持仓 - 实时估值（按流水回放快照）")

    d = st.date_input("as_of 日期", value=now_cn().date())
    date_str = d.isoformat()

    # 组合视图（快照口径）
    view = portfolio_realtime_view_as_of(date_str)
    pos_df = pd.DataFrame(view.get("positions", []))
    if not pos_df.empty:
        pct = pd.to_numeric(pos_df.get("est_change_pct", 0.0), errors="coerce").fillna(0.0)
        val = pd.to_numeric(pos_df.get("est_value", 0.0), errors="coerce").fillna(0.0)
        denom = 100.0 + pct
        today_est = 0.0
        ok = denom.abs() > 1e-9
        if ok.any():
            today_est = float((val[ok] * pct[ok] / denom[ok]).sum())
    else:
        today_est = 0.0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("总成本", f"{view['total_cost']:.2f}")
    c2.metric("预估市值", f"{view['total_est_value']:.2f}")
    c3.metric("预估盈亏", f"{view['total_est_pnl']:.2f}")
    c4.metric("预估盈亏率", f"{view['total_est_pnl_pct']:.2f}%")
    c5.metric("今日预计收益", f"{today_est:.2f}")
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
    df_pos = pd.DataFrame(view["positions"])
    if not df_pos.empty:
        amount_map = _load_input_amount_map(date_str)
        df_pos["fund_name"] = df_pos["code"].apply(
            lambda c: (get_fund_profile(str(c)).name or "").strip() if str(c).strip() else ""
        )
        # 若当天尚未保存“按金额输入”，则用成本金额回填（份额*成本净值），避免跟实时市值混淆。
        df_pos["input_amount"] = df_pos.apply(
            lambda r: float(amount_map.get(str(r.get("code", "")), 0.0) or 0.0)
            if float(amount_map.get(str(r.get("code", "")), 0.0) or 0.0) > 0
            else float(r.get("shares", 0.0) or 0.0) * float(r.get("avg_cost_nav", 0.0) or 0.0),
            axis=1,
        )
        if "realized_pnl" in df_pos.columns:
            df_pos = df_pos.drop(columns=["realized_pnl"])
        if "method" in df_pos.columns:
            df_pos = df_pos.drop(columns=["method"])
        # est_pnl is the total cumulative PnL under current estimate.
        df_pos["cumulative_pnl"] = df_pos["est_pnl"]
        # Current holding total value (no back-out of today's estimated pnl).
        df_pos["holding_total_current"] = df_pos["est_value"]
        # Approximate today's estimated PnL from current value and intraday pct.
        pct = pd.to_numeric(df_pos.get("est_change_pct", 0.0), errors="coerce").fillna(0.0)
        val = pd.to_numeric(df_pos.get("est_value", 0.0), errors="coerce").fillna(0.0)
        denom = 100.0 + pct
        df_pos["today_est_pnl"] = 0.0
        ok = denom.abs() > 1e-9
        df_pos.loc[ok, "today_est_pnl"] = val[ok] * pct[ok] / denom[ok]
        df_pos = df_pos.rename(
            columns={
                "code": "基金代码",
                "fund_name": "基金名称",
                "shares": "份额",
                "avg_cost_nav": "成本净值",
                "cumulative_pnl": "累计收益",
                "today_est_pnl": "今日预计收益",
                "holding_total_current": "持仓金额",
                "est_nav": "预估净值",
                "est_change_pct": "预估涨跌幅(%)",
                "confidence": "置信度",
                "warning": "提示",
                "est_time": "估值时间",
                "est_value": "预估市值",
                "est_pnl": "预估收益",
                "est_pnl_pct": "预估收益率(%)",
            }
        )
        # 明细展示顺序：核心字段优先，其余放后面
        primary_cols = [
            "基金代码",
            "基金名称",
            "持仓金额",
            "预估涨跌幅(%)",
            "今日预计收益",
            "累计收益",
            "估值时间",
        ]
        other_cols = [
            "份额",
            "成本净值",
            "预估净值",
            "预估收益率(%)",
            "置信度",
            "提示",
        ]
        display_cols = primary_cols + other_cols
        display_cols = [c for c in display_cols if c in df_pos.columns]
        df_pos = df_pos[display_cols]
        if "基金代码" in df_pos.columns:
            df_pos["基金代码"] = df_pos["基金代码"].astype(str)
        # Format est time to minutes.
        if "估值时间" in df_pos.columns:
            def _fmt_time(v):
                s = str(v or "").strip()
                if not s:
                    return ""
                if "T" in s:
                    s = s.replace("T", " ")
                return s[:16]
            df_pos["估值时间"] = df_pos["估值时间"].apply(_fmt_time)
        # 默认按持仓总当前从大到小，优先看大仓位。
        if "持仓金额" in df_pos.columns:
            df_pos = df_pos.sort_values(by="持仓金额", ascending=False, kind="stable")

        # 末行追加“总计”
        total_row = {c: pd.NA for c in df_pos.columns}
        if "基金代码" in total_row:
            total_row["基金代码"] = "总计"
        sum_cols = ["持仓金额", "今日预计收益", "累计收益"]
        for c in sum_cols:
            if c in df_pos.columns:
                total_row[c] = round(
                    float(pd.to_numeric(df_pos[c], errors="coerce").fillna(0.0).sum()),
                    2,
                )
        # 组合预估涨跌幅(%)：基于“今日预计收益 + 总金额(持仓金额)”反推。
        if "预估涨跌幅(%)" in df_pos.columns and "今日预计收益" in total_row and "持仓金额" in total_row:
            total_today = float(total_row.get("今日预计收益", 0.0) or 0.0)
            total_value = float(total_row.get("持仓金额", 0.0) or 0.0)
            base = total_value - total_today
            total_row["预估涨跌幅(%)"] = (total_today / base * 100.0) if abs(base) > 1e-9 else 0.0
        df_pos = pd.concat([df_pos, pd.DataFrame([total_row])], ignore_index=True)

        # Keep numeric values as numeric types for alignment.
        numeric_cols = [
            c for c in df_pos.columns
            if c not in ("基金代码", "基金名称", "估值时间", "提示")
        ]
        for c in numeric_cols:
            df_pos[c] = pd.to_numeric(df_pos[c], errors="coerce")
        # Round key numeric columns for display.
        for c in [
            "持仓金额",
            "预估涨跌幅(%)",
            "今日预计收益",
            "累计收益",
            "份额",
            "预估收益率(%)",
            "置信度",
        ]:
            if c in df_pos.columns:
                df_pos[c] = df_pos[c].round(2)
    detail_height = 38 + max(len(df_pos), 1) * 35
    if not df_pos.empty:
        def _color_row(row):
            try:
                x = float(row.get("今日预计收益", 0.0))
            except Exception:
                x = 0.0
            if x > 0:
                color = "red"
            elif x < 0:
                color = "green"
            else:
                color = "black"
            return [f"color: {color}"] * len(row)

        styler = df_pos.style
        if "今日预计收益" in df_pos.columns:
            styler = styler.apply(_color_row, axis=1)

        two_dec_cols = [
            "持仓金额",
            "预估涨跌幅(%)",
            "今日预计收益",
            "累计收益",
            "份额",
            "预估收益率(%)",
            "置信度",
        ]
        four_dec_cols = ["成本净值", "预估净值"]
        column_config = {
            c: st.column_config.NumberColumn(format="%.2f")
            for c in two_dec_cols
            if c in df_pos.columns
        }
        for c in four_dec_cols:
            if c in df_pos.columns:
                column_config[c] = st.column_config.NumberColumn(format="%.4f")
        st.dataframe(
            styler,
            width="stretch",
            hide_index=True,
            height=detail_height,
            column_config=column_config or None,
        )
    else:
        st.dataframe(df_pos, width="stretch", hide_index=True, height=detail_height)

    st.divider()
    st.subheader("编辑持仓（生成流水）")

    # === 关键：从快照拿默认值，避免误点保存写出脏流水 ===
    snaps = build_positions_as_of(date_str)
    snap_map = {s.code: s for s in snaps}

    # code input: watchlist or manual
    wl_codes = watchlist_list()
    code_opts = sorted(set(snap_map.keys()) | set(wl_codes))

    def _fmt_code(c: str) -> str:
        try:
            prof = get_fund_profile(c)
            n = (prof.name or "").strip()
        except Exception:
            n = ""
        if not n:
            n = f"\u57fa\u91d1{c}"
        return f"{c} - {n}"

    mode = st.radio("\u57fa\u91d1\u4ee3\u7801\u8f93\u5165", ["\u4ece\u81ea\u9009\u4e2d\u9009\u62e9", "\u624b\u52a8\u8f93\u5165"], horizontal=True)
    if mode == "\u624b\u52a8\u8f93\u5165":
        code = st.text_input("\u57fa\u91d1\u4ee3\u7801", value="", placeholder="\u4f8b\u5982\uff1a510300 / 000001").strip()
        if not code and code_opts:
            code = st.selectbox("\u5907\u9009\u5217\u8868", options=code_opts, format_func=_fmt_code)
    else:
        if code_opts:
            code = st.selectbox("\u81ea\u9009/\u5df2\u6709\u4ee3\u7801", options=code_opts, format_func=_fmt_code)
        else:
            code = st.text_input("\u57fa\u91d1\u4ee3\u7801", value="", placeholder="\u4f8b\u5982\uff1a510300 / 000001").strip()

    if not code:
        st.info("\u8bf7\u5148\u8f93\u5165\u57fa\u91d1\u4ee3\u7801\u6216\u5728\u81ea\u9009\u4e2d\u6dfb\u52a0\u57fa\u91d1\u3002")
        return

    cur = snap_map.get(code)
    cur_shares = float(cur.shares_end) if cur else 0.0
    cur_cost = float(cur.avg_cost_nav_end) if cur else 0.0
    cur_realized = float(cur.realized_pnl_end) if cur else 0.0
    editor_amount_map = _load_input_amount_map(date_str)
    default_record_amount = float(editor_amount_map.get(code, cur_shares * cur_cost) or 0.0)
    yday_date_str = (date.fromisoformat(date_str) - timedelta(days=1)).isoformat()
    yday_total_pnl_default = get_fund_cumulative_pnl_on(code, yday_date_str)
    if yday_total_pnl_default is None:
        yday_total_pnl_default = 0.0

    input_mode = st.radio("编辑方式", ["按金额/收益输入", "按份额/净值输入"], horizontal=True)

    amount_end_input = None
    record_amount_input = default_record_amount
    if input_mode == "按金额/收益输入":
        try:
            est = estimate_one(code)
            nav_for_calc = float(est.est_nav or 0.0)
            pct_for_calc = float(est.est_change_pct or 0.0)
        except Exception:
            nav_for_calc = 0.0
            pct_for_calc = 0.0
        if nav_for_calc <= 0:
            nav_for_calc = cur_cost if cur_cost > 0 else 1.0

        cur_value = cur_shares * nav_for_calc

        c1, c2 = st.columns(2)
        with c1:
            amount_end = st.number_input("当日结束持仓金额(元)", min_value=0.0, value=float(cur_value), step=100.0, format="%.2f")
        with c2:
            yday_total_pnl = st.number_input(
                "昨日累计收益(元)",
                value=float(yday_total_pnl_default),
                step=10.0,
                format="%.2f",
                key=f"yday_total_pnl_{date_str}_{code}",
            )

        # 用当日预估涨跌幅估算“今日收益”
        denom = 100.0 + pct_for_calc
        today_est_pnl = (amount_end * pct_for_calc / denom) if abs(denom) > 1e-9 else 0.0
        total_pnl_end = yday_total_pnl + today_est_pnl
        realized = cur_realized

        shares_end = (amount_end / nav_for_calc) if nav_for_calc > 0 else 0.0
        amount_end_input = float(amount_end)
        record_amount_input = float(amount_end)
        cost_value = amount_end + realized - total_pnl_end
        avg_cost = (cost_value / shares_end) if shares_end > 0 else 0.0

        st.caption(
            f"今日预估涨跌幅: {pct_for_calc:.2f}%；今日预估收益: {today_est_pnl:.2f} 元；"
            f"当日结束累计收益(自动): {total_pnl_end:.2f} 元；已实现收益沿用: {realized:.2f} 元"
        )
        st.caption(f"当前估值净值(用于换算): {nav_for_calc:.6f}；自动换算份额: {shares_end:.4f}；自动换算成本净值: {avg_cost:.6f}")
    else:
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
        record_amount_input = st.number_input(
            "录入持仓金额(展示口径)",
            min_value=0.0,
            value=float(default_record_amount),
            step=100.0,
            format="%.2f",
        )

    note = "UI编辑"
    st.caption("说明：此处生成的是“仓位校准流水”，用于把账本对齐到目标持仓，不代表真实成交价。")

    is_clearing = cur_shares > 0 and float(shares_end) == 0.0
    clear_confirmed = True
    if is_clearing:
        st.warning("检测到你把持仓从非 0 改为 0（清仓）。")
        clear_confirmed = st.checkbox(
            "我确认这是清仓操作，并同意写入校准流水",
            value=False,
            key=f"confirm_clear_{date_str}_{code}",
        )

    if st.button("保存编辑（写入流水）", type="primary", disabled=(is_clearing and not clear_confirmed)):

        apply_position_edit(
            effective_date=date_str,
            code=code,
            shares_end=float(shares_end),
            avg_cost_nav_end=float(avg_cost),
            realized_pnl_end=float(realized),
            note=note,
        )
        if amount_end_input is not None:
            _save_input_amount(date_str, code, amount_end_input)
        else:
            _save_input_amount(date_str, code, float(record_amount_input))
        st.success("已写入流水（adjustments.json），并可回放快照。")
        st.rerun()

    st.divider()
    st.subheader("删除持仓（危险操作）")
    st.caption("删除该基金全部历史流水记录，并清理录入持仓金额。")
    confirm_delete = st.checkbox("我确认删除该基金全部持仓流水", value=False, key=f"confirm_delete_{code}")
    if st.button("删除该基金全部流水", type="secondary", disabled=not confirm_delete):
        removed = _remove_adjustments_by_code_safe(code)
        _delete_input_amount_for_code(code)
        st.success(f"已删除 {code} 的 {removed} 条流水记录。")
        st.rerun()


render_portfolio()
