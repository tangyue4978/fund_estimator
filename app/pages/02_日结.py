import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from services.auth_guard import require_login
from services.trading_time import now_cn
from services.settlement_service import (
    finalize_estimated_close,
    settle_day,
    settle_pending_days,
    get_ledger_items,
)


st.set_page_config(page_title="Ledger", layout="wide")
require_login()


def fix_bad_sells_in_adjustments() -> int:
    from services import adjustment_service

    items = adjustment_service.list_adjustments()
    if not items:
        return 0

    shares = {}
    bad_ids = []

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
                bad_ids.append(str(a.get("id")))
            else:
                shares[code] = cur - sh

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
    st.title("日结台账 Daily Ledger")

    st.divider()
    st.subheader("维护工具")

    confirm_fix_bad_sells = st.checkbox("我确认删除检测到的超卖 SELL 流水", value=False)
    if st.button("一键修复：删除超卖 SELL 流水（防止回放穿仓）", disabled=not confirm_fix_bad_sells):
        n = fix_bad_sells_in_adjustments()
        st.toast(f"已删除异常 SELL 条数：{n}", icon="🧹")
        st.rerun()

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        d = st.date_input("选择日期", value=now_cn().date())
        date_str = d.isoformat()
    with col2:
        if st.button("生成当日收盘估算（estimated_only）", width="stretch"):
            try:
                finalize_estimated_close(date_str)
                st.toast("已生成/更新", icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"生成失败：{e}")
    with col3:
        if st.button("尝试结算所选日期（覆盖官方净值）", width="stretch"):
            try:
                _, cnt = settle_day(date_str)
                st.toast(f"结算覆盖条数：{cnt}", icon="📌")
                st.rerun()
            except Exception as e:
                st.error(f"结算失败：{e}")

    if st.button("扫描近7天结算（settle_pending_days）"):
        try:
            _, total = settle_pending_days(7)
            st.toast(f"共覆盖：{total}", icon="🔁")
            st.rerun()
        except Exception as e:
            st.error(f"扫描结算失败：{e}")

    items = get_ledger_items()
    if not items:
        st.info("daily_ledger 为空：先去 Portfolio 编辑持仓生成流水，再来这里生成日结。")
        return

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
    st.dataframe(filtered_items, width="stretch", hide_index=True)


render_ledger()
