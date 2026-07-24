import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
import pandas as pd

from app.ui import (
    action_error,
    apply_app_style,
    configure_page,
    danger_container,
    dataframe_height,
    degraded_notice,
    empty_state,
    page_header,
    section_header,
    settlement_status_label,
)
from services.auth_guard import require_login
from services.cloud_status_service import get_cloud_error
from services.trading_time import now_cn
from services.settlement_service import (
    finalize_estimated_close,
    settle_day,
    settle_pending_days,
    get_ledger_items,
)


configure_page("日结台账", icon="🧾")
apply_app_style()
require_login(render_sidebar=False)


def preview_bad_sells_in_adjustments() -> list[dict]:
    from services import adjustment_service

    items = adjustment_service.list_adjustments()
    if not items:
        return []

    shares = {}
    bad_rows = []

    items_sorted = sorted(items, key=lambda x: (str(x.get("effective_date", "")), str(x.get("created_at", ""))))
    for a in items_sorted:
        t = str(a.get("type", ""))
        code = str(a.get("code", ""))
        sh = float(a.get("shares", 0.0) or 0.0)

        cur = float(shares.get(code, 0.0))
        if t == "BUY":
            shares[code] = cur + sh
        elif t == "SELL":
            if sh > cur + 1e-9:
                bad_rows.append(
                    {
                        "id": str(a.get("id", "")),
                        "code": code,
                        "effective_date": str(a.get("effective_date", "")),
                        "sell_shares": sh,
                        "shares_before": cur,
                        "excess_shares": sh - cur,
                        "price": float(a.get("price", 0.0) or 0.0),
                        "note": str(a.get("note", "") or ""),
                        "source": str(a.get("source", "") or ""),
                        "created_at": str(a.get("created_at", "") or ""),
                    }
                )
            else:
                shares[code] = cur - sh

    return bad_rows


def fix_bad_sells_in_adjustments(bad_rows: list[dict] | None = None) -> int:
    from services import adjustment_service

    rows = bad_rows if bad_rows is not None else preview_bad_sells_in_adjustments()
    bad_ids = [str(row.get("id", "")).strip() for row in rows if isinstance(row, dict) and str(row.get("id", "")).strip()]
    if not bad_ids:
        return 0

    removed = 0
    for rid in bad_ids:
        try:
            adjustment_service.remove_adjustment(rid)
            removed += 1
        except Exception:
            continue
    return removed


def render_ledger():
    page_header(
        "日结台账",
        "生成每日收盘估算，并在官方净值公布后完成结算覆盖和历史核对。",
        eyebrow="收盘与结算",
    )

    bad_sell_rows = preview_bad_sells_in_adjustments()
    adjustments_err = get_cloud_error("adjustments")
    if adjustments_err:
        degraded_notice(
            "持仓流水暂时不可用，超卖检查结果可能不完整。",
            adjustments_err,
            detail_label="持仓流水技术详情",
        )
    with st.expander(
        f"异常流水维护{f' · {len(bad_sell_rows)} 条待处理' if bad_sell_rows else ''}",
        expanded=bool(bad_sell_rows),
    ):
        if bad_sell_rows:
            st.warning(f"检测到 {len(bad_sell_rows)} 条卖出份额超过当时持仓的流水，请先核对明细。")
            preview_df = pd.DataFrame(bad_sell_rows)
            for col in ["sell_shares", "shares_before", "excess_shares", "price"]:
                if col in preview_df.columns:
                    preview_df[col] = pd.to_numeric(preview_df[col], errors="coerce").round(4)
            preview_show = preview_df[
                [
                    "effective_date",
                    "code",
                    "sell_shares",
                    "shares_before",
                    "excess_shares",
                    "price",
                    "source",
                    "note",
                    "id",
                ]
            ].rename(
                columns={
                    "effective_date": "生效日期",
                    "code": "基金代码",
                    "sell_shares": "卖出份额",
                    "shares_before": "卖出前份额",
                    "excess_shares": "超出份额",
                    "price": "成交价",
                    "source": "来源",
                    "note": "备注",
                    "id": "流水 ID",
                }
            )
            st.dataframe(
                preview_show,
                width="stretch",
                hide_index=True,
                height=dataframe_height(len(preview_show), max_rows=8),
            )
            with danger_container("delete_bad_sells"):
                st.markdown("**永久删除上表中的异常卖出流水**")
                st.caption("删除后无法在应用内恢复，且可能改变后续日期的持仓回放结果。")
                confirm_fix_bad_sells = st.checkbox(
                    "我已核对明细，并确认永久删除这些流水",
                    value=False,
                )
                if st.button(
                    "永久删除异常卖出流水",
                    type="primary",
                    disabled=not confirm_fix_bad_sells,
                    width="stretch",
                ):
                    n = fix_bad_sells_in_adjustments(bad_sell_rows)
                    st.toast(f"已删除 {n} 条异常卖出流水", icon="🧹")
                    st.rerun()
        elif not adjustments_err:
            st.success("未检测到卖出份额超过当时持仓的流水。")

    section_header("生成与结算", "先生成当日收盘估算；官方净值公布后，再按日期完成结算覆盖。")
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        d = st.date_input("选择日期", value=now_cn().date(), max_value=now_cn().date())
        date_str = d.isoformat()
    with col2:
        is_today = d == now_cn().date()
        if st.button(
            "生成今日收盘估算",
            type="primary",
            width="stretch",
            disabled=not is_today,
            help="按今天的持仓和估值生成待结算记录。",
        ):
            try:
                finalize_estimated_close(date_str)
                st.toast("已生成/更新", icon="✅")
                st.rerun()
            except Exception as e:
                action_error("生成失败，未确认写入任何新的收盘估算。", e)
        if not is_today:
            st.caption("历史日期不能套用今天行情；可使用右侧“覆盖官方净值”进行结算。")
    with col3:
        if st.button(
            "按官方净值结算所选日期",
            width="stretch",
            help="仅在官方净值已经可用时覆盖收盘估算。",
        ):
            try:
                _, cnt = settle_day(date_str)
                st.toast(f"结算覆盖条数：{cnt}", icon="📌")
                st.rerun()
            except Exception as e:
                action_error("结算失败，请核对官方净值和云端状态后重试。", e)

    if st.button("扫描并结算近 7 天待处理记录", width="stretch"):
        try:
            _, total = settle_pending_days(7)
            st.toast(f"共覆盖：{total}", icon="🔁")
            st.rerun()
        except Exception as e:
            action_error("扫描结算失败，请稍后重试。", e)

    items = get_ledger_items()
    ledger_err = get_cloud_error("daily_ledger")
    if ledger_err:
        degraded_notice(
            "云端日结台账暂时不可用，当前可能显示最近一次成功读取的数据。",
            ledger_err,
            detail_label="日结台账技术详情",
        )
        if not items:
            return
    if not items:
        empty_state(
            "还没有日结记录",
            "先到“持仓管理”录入持仓，再回到这里生成今天的收盘估算。",
        )
        return

    st.divider()
    section_header("台账记录", "默认按日期和基金代码倒序排列，可按结算状态或基金代码筛选。")
    items = sorted(items, key=lambda x: (x.get("date", ""), x.get("code", "")), reverse=True)
    total_count = len(items)
    settled_count = sum(1 for item in items if str(item.get("settle_status", "")) == "settled")
    pending_count = sum(1 for item in items if str(item.get("settle_status", "")) == "estimated_only")
    latest_date = str(items[0].get("date", "-")) if items else "-"
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("台账记录", total_count)
    m2.metric("已覆盖官方净值", settled_count)
    m3.metric("待覆盖估算", pending_count)
    m4.metric("最近日期", latest_date)

    status_label = st.radio("台账状态", ["全部", "已覆盖官方净值", "待覆盖估算"], horizontal=True)
    code_query = st.text_input("按基金代码筛选", value="", placeholder="输入代码片段，例如 510300")
    filtered_items = items
    if status_label == "已覆盖官方净值":
        filtered_items = [item for item in filtered_items if str(item.get("settle_status", "")) == "settled"]
    elif status_label == "待覆盖估算":
        filtered_items = [item for item in filtered_items if str(item.get("settle_status", "")) == "estimated_only"]
    code_query = code_query.strip()
    if code_query:
        filtered_items = [item for item in filtered_items if code_query in str(item.get("code", ""))]

    st.caption(f"当前显示 {len(filtered_items)} / {len(items)} 条记录。")
    if not filtered_items:
        empty_state("没有符合条件的记录", "清空基金代码或切换结算状态后再试。")
        return

    ledger_df = pd.DataFrame(filtered_items)
    if "settle_status" in ledger_df.columns:
        ledger_df["settle_status"] = ledger_df["settle_status"].apply(settlement_status_label)
    ledger_df = ledger_df.drop(columns=["user_id"], errors="ignore").rename(
        columns={
            "date": "日期",
            "code": "基金代码",
            "shares_end": "期末份额",
            "avg_cost_nav_end": "期末成本净值",
            "realized_pnl_end": "期末已实现收益",
            "estimated_nav_close": "收盘估算净值",
            "estimated_pnl_close": "收盘估算收益",
            "official_nav": "官方净值",
            "official_pnl": "官方收益",
            "settle_status": "结算状态",
            "updated_at": "更新时间",
        }
    )
    preferred_columns = [
        "日期",
        "基金代码",
        "结算状态",
        "期末份额",
        "期末成本净值",
        "收盘估算净值",
        "官方净值",
        "收盘估算收益",
        "官方收益",
        "期末已实现收益",
        "更新时间",
    ]
    ledger_df = ledger_df[[col for col in preferred_columns if col in ledger_df.columns]]
    st.dataframe(
        ledger_df,
        width="stretch",
        hide_index=True,
        height=dataframe_height(len(ledger_df), max_rows=12),
        column_config={
            "期末份额": st.column_config.NumberColumn(format="%.4f"),
            "期末成本净值": st.column_config.NumberColumn(format="%.6f"),
            "收盘估算净值": st.column_config.NumberColumn(format="%.6f"),
            "官方净值": st.column_config.NumberColumn(format="%.6f"),
            "收盘估算收益": st.column_config.NumberColumn(format="%.2f"),
            "官方收益": st.column_config.NumberColumn(format="%.2f"),
            "期末已实现收益": st.column_config.NumberColumn(format="%.2f"),
        },
    )


render_ledger()
