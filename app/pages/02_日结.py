import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from datetime import date

from storage import paths
from storage.json_store import load_json
from services.settlement_service import finalize_estimated_close, settle_day, settle_pending_days


st.set_page_config(page_title="Ledger", layout="wide")

def fix_bad_sells_in_adjustments() -> int:
    from storage import paths
    from storage.json_store import load_json, save_json

    p = paths.file_adjustments()
    data = load_json(p, fallback={"items": []})
    items = data.get("items", [])
    if not isinstance(items, list):
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

    new_items = [x for x in items if str(x.get("id")) not in set(bad_ids)]
    data["items"] = new_items
    save_json(p, data)
    return len(bad_ids)


def render_ledger():    
    st.title("日结台账 Daily Ledger")

    st.divider()
    st.subheader("维护工具")

    if st.button("一键修复：删除超卖 SELL 流水（防止回放崩溃）"):
        n = fix_bad_sells_in_adjustments()
        st.toast(f"已删除异常 SELL 条数：{n}", icon="🧹")
        st.rerun()

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        d = st.date_input("选择日期", value=date.today())
        date_str = d.isoformat()
    with col2:
        if st.button("生成当日收盘估算（estimated_only）", width="stretch"):
            finalize_estimated_close(date_str)
            st.toast("已生成/更新", icon="✅")
            st.rerun()
    with col3:
        if st.button("尝试结算所选日期（覆盖官方净值）", width="stretch"):
            _, cnt = settle_day(date_str)
            st.toast(f"结算覆盖条数：{cnt}", icon="🧾")
            st.rerun()

    if st.button("扫描近7天结算（settle_pending_days）"):
        _, total = settle_pending_days(7)
        st.toast(f"共覆盖：{total}", icon="🔁")
        st.rerun()

    data = load_json(paths.file_daily_ledger(), fallback={"items": []})
    items = data.get("items", [])
    if not items:
        st.info("daily_ledger 为空：先去 Portfolio 编辑持仓生成流水，再来这里生成日结。")
        return

    items = sorted(items, key=lambda x: (x.get("date", ""), x.get("code", "")), reverse=True)
    st.dataframe(items, width="stretch", hide_index=True)


render_ledger()
