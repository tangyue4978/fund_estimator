import sys
import time
from pathlib import Path

# ---- bootstrap: ensure project root in sys.path ----
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go

from services.portfolio_service import portfolio_realtime_view_as_of
from services.cloud_status_service import get_cloud_error
from services.edit_bridge_service import apply_position_edit
from services.portfolio_import_service import (
    apply_import_preview,
    build_import_preview,
    holdings_image_import_enabled,
)
from services.snapshot_service import build_positions_as_of
from services.watchlist_service import watchlist_list
from services.accuracy_service import portfolio_gap_summary, portfolio_gap_table
from services.fund_service import get_fund_profile
from services.estimation_service import estimate_one
from services.history_service import get_fund_cumulative_pnl_on
from services.vision_holdings_service import analyze_holdings_image
from services import adjustment_service
from services.auth_guard import require_login
from services.trading_time import cn_market_phase, now_cn
from config import settings


st.set_page_config(page_title="Portfolio", layout="wide")
require_login()


def _portfolio_refresh_sec() -> int:
    phase = cn_market_phase(now_cn())
    if phase == "trading":
        refresh_raw = getattr(settings, "PORTFOLIO_AUTO_REFRESH_SEC", 30)
    elif phase == "lunch":
        refresh_raw = getattr(settings, "PORTFOLIO_AUTO_REFRESH_SEC_LUNCH", 300)
    else:
        refresh_raw = getattr(settings, "PORTFOLIO_AUTO_REFRESH_SEC_NON_TRADING", 900)
    try:
        return max(0, int(refresh_raw))
    except Exception:
        return 30 if phase == "trading" else (300 if phase == "lunch" else 900)


def _portfolio_fragment_refresh_enabled() -> bool:
    auto_on = bool(getattr(settings, "PORTFOLIO_AUTO_REFRESH_ENABLED", True))
    return auto_on and _portfolio_refresh_sec() > 0 and hasattr(st, "fragment")


def _remove_adjustments_by_code_safe(code: str) -> int:
    code = (code or "").strip()
    if not code:
        return 0
    fn = getattr(adjustment_service, "remove_adjustments_by_code", None)
    if callable(fn):
        return int(fn(code) or 0)

    return 0


def _import_mode_value(label: str) -> str:
    return "sync" if "同步" in str(label or "") else "delta"


def _clear_portfolio_view_cache() -> None:
    st.session_state.pop("_portfolio_view_cache", None)


def _render_image_import(date_str: str) -> None:
    st.subheader("图片导入持仓")
    st.caption("支持两种模式：同步持仓会覆盖到图片识别出的最终仓位；加减仓会在当前持仓基础上做增减。")

    if not holdings_image_import_enabled():
        st.info("未启用图片识别。请在 `.streamlit/secrets.toml` 配置 `GEMINI_API_KEY`，可选配置 `GEMINI_MODEL` 和 `GEMINI_API_BASE_URL`。")
        return

    mode_label = st.radio(
        "图片导入方式",
        ["同步持仓（覆盖原持仓）", "加减仓（在原持仓上增减）"],
        horizontal=True,
        key="holding_image_mode_label",
    )
    import_mode = _import_mode_value(mode_label)
    files = st.file_uploader(
        "上传持仓截图",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="holding_image_files",
    )
    if files:
        st.caption(f"已选择 {len(files)} 张图片，请点击“识别图片”。")

    col_recognize, col_clear = st.columns([1, 1])
    with col_recognize:
        if st.button("识别图片", type="primary", disabled=not files, width="stretch"):
            rows = []
            warnings = []
            status = {"level": "info", "message": "开始识别图片。"}
            with st.spinner("正在识别图片..."):
                for file in files or []:
                    try:
                        result = analyze_holdings_image(
                            image_bytes=file.getvalue(),
                            mime_type=file.type or "image/png",
                            filename=file.name,
                            mode=import_mode,
                        )
                        for item in result.get("rows", []):
                            if not isinstance(item, dict):
                                continue
                            row = dict(item)
                            row["source_image"] = file.name
                            rows.append(row)
                        for warn in result.get("warnings", []):
                            if str(warn or "").strip():
                                warnings.append(f"{file.name}: {warn}")
                    except Exception as e:
                        warnings.append(f"{file.name}: 识别失败 - {e}")
            st.session_state["holding_image_rows"] = rows
            st.session_state["holding_image_warnings"] = warnings
            st.session_state["holding_image_preview"] = None
            st.session_state["holding_image_last_run"] = {
                "file_count": len(files or []),
                "row_count": len(rows),
                "warning_count": len(warnings),
                "mode": import_mode,
            }
            if rows:
                status = {"level": "success", "message": f"识别完成：共提取 {len(rows)} 条记录。"}
            elif warnings:
                status = {"level": "error", "message": "识别未提取到有效记录，请检查下方报错。"}
            else:
                status = {"level": "warning", "message": "识别完成，但没有提取到任何持仓记录。请更换更清晰的截图，或手工补录。"}
            st.session_state["holding_image_status"] = status

    with col_clear:
        if st.button("清空识别结果", width="stretch"):
            st.session_state.pop("holding_image_rows", None)
            st.session_state.pop("holding_image_warnings", None)
            st.session_state.pop("holding_image_preview", None)
            st.session_state.pop("holding_image_status", None)
            st.session_state.pop("holding_image_last_run", None)
            st.rerun()

    status = st.session_state.get("holding_image_status")
    if isinstance(status, dict):
        level = str(status.get("level", "info"))
        message = str(status.get("message", "")).strip()
        if message:
            if level == "success":
                st.success(message)
            elif level == "warning":
                st.warning(message)
            elif level == "error":
                st.error(message)
            else:
                st.info(message)

    last_run = st.session_state.get("holding_image_last_run")
    if isinstance(last_run, dict):
        st.caption(
            "最近一次识别："
            f" 模式={last_run.get('mode', '-')}"
            f"，图片数={last_run.get('file_count', 0)}"
            f"，记录数={last_run.get('row_count', 0)}"
            f"，警告数={last_run.get('warning_count', 0)}"
        )

    warnings = st.session_state.get("holding_image_warnings", [])
    for warn in warnings:
        st.warning(str(warn))

    rows = st.session_state.get("holding_image_rows", [])
    if not rows:
        return

    raw_df = pd.DataFrame(rows)
    default_cols = ["source_image", "code", "fund_name", "confidence", "notes"]
    if import_mode == "sync":
        editable_cols = ["shares", "avg_cost_nav", "amount", "cumulative_pnl", "daily_pnl", "pnl_pct"]
    else:
        editable_cols = ["delta_shares", "delta_amount", "avg_price", "side"]
    for col in default_cols + editable_cols:
        if col not in raw_df.columns:
            raw_df[col] = None
    raw_df = raw_df[default_cols + editable_cols]

    st.caption("识别结果支持手工修正，导入前建议核对基金代码和数值。")
    edited_df = st.data_editor(
        raw_df,
        width="stretch",
        hide_index=True,
        num_rows="dynamic",
        key="holding_image_editor",
        column_config={
            "confidence": st.column_config.NumberColumn("置信度", format="%.2f"),
        },
    )

    if st.button("生成导入预览", width="stretch"):
        preview = build_import_preview(
            rows=edited_df.where(pd.notnull(edited_df), None).to_dict("records"),
            mode=import_mode,
            effective_date=date_str,
        )
        st.session_state["holding_image_preview"] = preview

    preview = st.session_state.get("holding_image_preview")
    if not isinstance(preview, dict):
        return
    if preview.get("mode") != import_mode or preview.get("effective_date") != date_str:
        st.info("导入模式或日期已变化，请重新点击“生成导入预览”。")
        return

    preview_rows = []
    for item in preview.get("rows", []):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["warnings"] = "；".join(row.get("warnings", []) or [])
        row["errors"] = "；".join(row.get("errors", []) or [])
        preview_rows.append(row)

    if preview_rows:
        preview_df = pd.DataFrame(preview_rows)
        show_cols = [
            "code",
            "fund_name",
            "operation",
            "current_shares",
            "delta_shares",
            "target_shares",
            "target_avg_cost_nav",
            "target_realized_pnl",
            "recognized_amount",
            "recognized_cumulative_pnl",
            "recognized_pnl_pct",
            "warnings",
            "errors",
        ]
        show_cols = [c for c in show_cols if c in preview_df.columns]
        st.dataframe(
            preview_df[show_cols],
            width="stretch",
            hide_index=True,
            column_config={
                "current_shares": st.column_config.NumberColumn(format="%.4f"),
                "delta_shares": st.column_config.NumberColumn(format="%.4f"),
                "target_shares": st.column_config.NumberColumn(format="%.4f"),
                "target_avg_cost_nav": st.column_config.NumberColumn(format="%.6f"),
                "target_realized_pnl": st.column_config.NumberColumn(format="%.4f"),
                "recognized_amount": st.column_config.NumberColumn(format="%.2f"),
                "recognized_cumulative_pnl": st.column_config.NumberColumn(format="%.2f"),
                "recognized_pnl_pct": st.column_config.NumberColumn(format="%.2f"),
            },
        )

    if int(preview.get("error_count", 0) or 0) > 0:
        st.error(f"预览中有 {preview['error_count']} 条错误，请先修正后再导入。")

    clear_count = int(preview.get("clear_count", 0) or 0)
    clear_confirmed = True
    if import_mode == "sync" and clear_count > 0:
        st.warning(f"同步持仓模式会额外清零 {clear_count} 个未出现在图片中的现有持仓。")
        clear_confirmed = st.checkbox(
            "我确认同步持仓需要覆盖未识别到的现有持仓",
            value=False,
            key="holding_image_sync_confirm",
        )

    apply_disabled = (
        int(preview.get("valid_count", 0) or 0) <= 0
        or int(preview.get("error_count", 0) or 0) > 0
        or (import_mode == "sync" and clear_count > 0 and not clear_confirmed)
    )
    if st.button("确认导入图片持仓", type="primary", disabled=apply_disabled, width="stretch"):
        try:
            with st.spinner("正在写入持仓流水..."):
                result = apply_import_preview(preview)
            st.success(f"导入完成：已写入 {result['applied']} 条，跳过 {result['skipped']} 条。")
            st.session_state.pop("holding_image_preview", None)
            _clear_portfolio_view_cache()
            st.rerun()
        except Exception as e:
            st.error(f"导入失败：{e}")


def _load_portfolio_view(date_str: str) -> tuple[dict, float]:
    now_ts = time.time()
    today = now_cn().date().isoformat()
    ttl = 8.0 if date_str == today else 60.0
    cache = st.session_state.get("_portfolio_view_cache")
    if isinstance(cache, dict) and cache.get("date") == date_str and (now_ts - float(cache.get("ts", 0.0))) <= ttl:
        view_cached = cache.get("view", {})
        if isinstance(view_cached, dict):
            return view_cached, float(cache.get("today_est", 0.0) or 0.0)

    view = portfolio_realtime_view_as_of(date_str)
    pos_df = pd.DataFrame(view.get("positions", []))
    if pos_df.empty:
        st.session_state["_portfolio_view_cache"] = {"date": date_str, "ts": now_ts, "view": view, "today_est": 0.0}
        return view, 0.0
    pct = pd.to_numeric(pos_df.get("est_change_pct", 0.0), errors="coerce").fillna(0.0)
    val = pd.to_numeric(pos_df.get("est_value", 0.0), errors="coerce").fillna(0.0)
    denom = 100.0 + pct
    today_est = 0.0
    ok = denom.abs() > 1e-9
    if ok.any():
        today_est = float((val[ok] * pct[ok] / denom[ok]).sum())
    st.session_state["_portfolio_view_cache"] = {"date": date_str, "ts": now_ts, "view": view, "today_est": today_est}
    return view, today_est


def _render_live_summary(date_str: str) -> None:
    view, today_est = _load_portfolio_view(date_str)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("总成本", f"{view['total_cost']:.2f}")
    c2.metric("预估市值", f"{view['total_est_value']:.2f}")
    c3.metric("预估盈亏", f"{view['total_est_pnl']:.2f}")
    c4.metric("预估盈亏率", f"{view['total_est_pnl_pct']:.2f}%")
    c5.metric("今日预计收益", f"{today_est:.2f}")
    st.caption(f"估值覆盖率：{view['realtime_coverage_value_pct']:.2f}%")


def _render_live_detail(date_str: str) -> None:
    view, _ = _load_portfolio_view(date_str)
    st.subheader("明细")
    df_pos = pd.DataFrame(view["positions"])
    if not df_pos.empty:
        df_pos["fund_name"] = df_pos["code"].apply(
            lambda c: ((get_fund_profile(str(c)).name or "").strip() or f"基金{str(c).strip()}") if str(c).strip() else ""
        )
        df_pos["input_amount"] = df_pos.apply(
            lambda r: float(r.get("shares", 0.0) or 0.0) * float(r.get("avg_cost_nav", 0.0) or 0.0),
            axis=1,
        )
        if "realized_pnl" in df_pos.columns:
            df_pos = df_pos.drop(columns=["realized_pnl"])
        if "method" in df_pos.columns:
            df_pos = df_pos.drop(columns=["method"])
        df_pos["cumulative_pnl"] = df_pos["est_pnl"]
        df_pos["holding_total_current"] = df_pos["est_value"]
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
        if "估值时间" in df_pos.columns:
            def _fmt_time(v):
                s = str(v or "").strip()
                if not s:
                    return ""
                if "T" in s:
                    s = s.replace("T", " ")
                return s[:16]
            df_pos["估值时间"] = df_pos["估值时间"].apply(_fmt_time)
        if "持仓金额" in df_pos.columns:
            df_pos = df_pos.sort_values(by="持仓金额", ascending=False, kind="stable")

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
        if "预估涨跌幅(%)" in df_pos.columns and "今日预计收益" in total_row and "持仓金额" in total_row:
            total_today = float(total_row.get("今日预计收益", 0.0) or 0.0)
            total_value = float(total_row.get("持仓金额", 0.0) or 0.0)
            base = total_value - total_today
            total_row["预估涨跌幅(%)"] = (total_today / base * 100.0) if abs(base) > 1e-9 else 0.0
        df_pos = pd.concat([df_pos, pd.DataFrame([total_row])], ignore_index=True)

        numeric_cols = [
            c for c in df_pos.columns
            if c not in ("基金代码", "基金名称", "估值时间", "提示")
        ]
        for c in numeric_cols:
            df_pos[c] = pd.to_numeric(df_pos[c], errors="coerce")
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

def render_portfolio():
    st.title("持仓 - 实时估值（按流水回放快照）")
    portfolio_ledger_err = get_cloud_error("portfolio_ledger")
    daily_ledger_err = get_cloud_error("daily_ledger")
    watchlist_err = get_cloud_error("watchlist")
    adjustments_err = get_cloud_error("adjustments")
    if portfolio_ledger_err:
        st.warning(f"历史日结账本读取失败，过去日期视图可能缺少估值数据：{portfolio_ledger_err}")
    if daily_ledger_err:
        st.warning(f"日结数据读取失败，历史收益与误差分析可能为空：{daily_ledger_err}")
    if watchlist_err:
        st.warning(f"自选列表读取失败，编辑区候选基金可能不完整：{watchlist_err}")
    if adjustments_err:
        st.warning(f"持仓流水读取失败，当前显示的是最近一次成功读取的数据：{adjustments_err}")

    d = st.date_input("as_of 日期（用于编辑/回放）", value=now_cn().date())
    date_str = d.isoformat()
    live_date_str = now_cn().date().isoformat()
    is_today_view = date_str == live_date_str
    if is_today_view:
        st.caption(f"当前展示当日实时估值：{live_date_str}")
    else:
        st.info("当前展示所选历史日期的快照/日结数据；实时估值只用于当日。")

    refresh_sec = _portfolio_refresh_sec()
    use_fragment_refresh = is_today_view and _portfolio_fragment_refresh_enabled()
    live_summary_area = st.container()
    if use_fragment_refresh:
        @st.fragment(run_every=f"{refresh_sec}s")
        def _live_summary_fragment() -> None:
            with live_summary_area:
                _render_live_summary(date_str)

        _live_summary_fragment()
    else:
        with live_summary_area:
            _render_live_summary(date_str)

    st.subheader("组合口径：估算收盘 vs 官方净值")

    ps = portfolio_gap_summary(days_back=120)
    latest = None
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
        latest_row = latest if isinstance(latest, dict) else (rows[-1] if rows else None)
        if isinstance(latest_row, dict):
            abs_gap = float(latest_row.get("abs_gap_pct", 0.0) or 0.0)
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


    live_detail_area = st.container()
    if use_fragment_refresh:
        @st.fragment(run_every=f"{refresh_sec}s")
        def _live_detail_fragment() -> None:
            with live_detail_area:
                _render_live_detail(date_str)

        _live_detail_fragment()
    else:
        with live_detail_area:
            _render_live_detail(date_str)

    st.divider()
    _render_image_import(date_str)

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
    default_record_amount = float(cur_shares * cur_cost)
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
        try:
            apply_position_edit(
                effective_date=date_str,
                code=code,
                shares_end=float(shares_end),
                avg_cost_nav_end=float(avg_cost),
                realized_pnl_end=float(realized),
                note=note,
            )
            st.success("已写入云端流水，并可回放快照。")
            _clear_portfolio_view_cache()
            st.rerun()
        except Exception as e:
            st.error(f"保存失败：{e}")

    st.divider()
    st.subheader("删除持仓（危险操作）")
    st.caption("删除该基金全部历史流水记录，并清理录入持仓金额。")
    confirm_delete = st.checkbox("我确认删除该基金全部持仓流水", value=False, key=f"confirm_delete_{code}")
    if st.button("删除该基金全部流水", type="secondary", disabled=not confirm_delete):
        try:
            removed = _remove_adjustments_by_code_safe(code)
            st.success(f"已删除 {code} 的 {removed} 条流水记录。")
            _clear_portfolio_view_cache()
            st.rerun()
        except Exception as e:
            st.error(f"删除失败：{e}")


render_portfolio()
