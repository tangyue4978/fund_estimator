import sys
import os
import subprocess
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

# ---- bootstrap: ensure project root in sys.path ----
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # pragma: no cover
    st_autorefresh = None

from services.watchlist_service import watchlist_list
from services.estimation_service import estimate_one
from services.intraday_service import intraday_load_fund_series, record_intraday_point
from services.trading_time import now_cn, is_cn_trading_time
from services.history_service import fund_history
from services.settlement_service import get_ledger_row
from storage import paths
from services.auth_guard import require_login
from services.accuracy_service import fund_gap_summary, guess_gap_reasons, fund_gap_table
from services.fund_service import get_fund_profile
from config import settings


st.set_page_config(page_title="Fund Detail", layout="wide")
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


# auto refresh
_auto_on = bool(getattr(settings, "FUND_DETAIL_AUTO_REFRESH_ENABLED", True))
_refresh_raw = getattr(settings, "FUND_DETAIL_AUTO_REFRESH_SEC", 30)
_refresh_sec = int(30 if _refresh_raw is None else _refresh_raw)
if _auto_on and _refresh_sec > 0:
    _apply_silent_autorefresh_style()
    if st_autorefresh is not None:
        st_autorefresh(interval=int(_refresh_sec) * 1000, key="fund_detail_autorefresh")
    elif hasattr(st, "autorefresh"):
        st.autorefresh(interval=int(_refresh_sec) * 1000, key="fund_detail_autorefresh")

def _pick_code_from_query_or_select() -> str:
    # Streamlit 新旧版本兼容 query params
    code = ""
    try:
        qp = st.query_params  # 新版
        code = qp.get("code", "")
        if isinstance(code, list):
            code = code[0] if code else ""
    except Exception:
        try:
            qp = st.experimental_get_query_params()  # 旧版
            code = qp.get("code", [""])[0]
        except Exception:
            code = ""

    code = (code or "").strip()
    wl = watchlist_list()
    options = list(wl)

    if code and code not in options:
        options = [code] + options

    def _fmt_option(c: str) -> str:
        try:
            p = get_fund_profile(c)
            n = (p.name or "").strip()
        except Exception:
            n = ""
        if not n:
            n = f"\u57fa\u91d1{c}"
        return f"{c} - {n}"

    if not options:
        return st.text_input("基金代码", value="", placeholder="例如：510300 / 000001").strip()

    return st.selectbox("基金代码", options=options, index=0 if not code else options.index(code), format_func=_fmt_option)




def _downsample(points: list, max_points: int) -> list:
    if not isinstance(points, list) or max_points <= 0:
        return []
    n = len(points)
    if n <= max_points:
        return points
    step = max(1, n // max_points)
    sampled = points[::step]
    return sampled[-max_points:]


def _is_trading_time_hhmmss(t: str) -> bool:
    try:
        hh, mm, ss = t.split(":")
        h = int(hh)
        m = int(mm)
        s = int(ss)
    except Exception:
        return False
    total = h * 3600 + m * 60 + s
    am_start = 9 * 3600 + 30 * 60
    am_end = 11 * 3600 + 30 * 60
    pm_start = 13 * 3600
    pm_end = 15 * 3600
    return (am_start <= total <= am_end) or (pm_start <= total <= pm_end)


def _filter_trading_session(points: list) -> list:
    if not isinstance(points, list):
        return []
    out = []
    for it in points:
        t = str(it.get("t", "")).strip()
        if not t:
            continue
        if _is_trading_time_hhmmss(t):
            out.append(it)
    return out


def _collector_running() -> bool:
    try:
        if hasattr(paths, "file_collector_pid"):
            p = Path(paths.file_collector_pid())
        elif hasattr(paths, "status_dir"):
            uid = str(paths.current_user_id()).strip() or "public"
            p = Path(paths.status_dir()) / f"collector_{uid}.pid"
        elif hasattr(paths, "runtime_root"):
            uid = str(paths.current_user_id()).strip() or "public"
            p = Path(paths.runtime_root()) / "status" / f"collector_{uid}.pid"
        else:
            uid = "public"
            p = PROJECT_ROOT / "storage" / "status" / f"collector_{uid}.pid"
        raw = p.read_text(encoding="utf-8").strip()
        pid = int(raw) if raw else 0
    except Exception:
        return False

    if pid <= 0:
        return False

    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            out = (proc.stdout or "").strip()
            return bool(out) and (not out.upper().startswith("INFO:")) and (f'"{pid}"' in out)
        except Exception:
            return False

    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _is_web_runtime() -> bool:
    return bool(os.getenv("STREAMLIT_SHARING_MODE", "").strip())


def _read_ledger_status(code: str, date_str: str) -> dict:
    return get_ledger_row(date_str, code)


def render():
    st.title("基金详情")

    code = _pick_code_from_query_or_select()
    if not code:
        st.info("请先输入基金代码或在自选中添加基金。")
        return

    # --- top: realtime quote ---
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

    # Silent page-side sampling write: write after fetch, no toast/rerun.
    if est:
        if "detail_last_sample_ts" not in st.session_state:
            st.session_state["detail_last_sample_ts"] = {}
        _last_map = st.session_state["detail_last_sample_ts"]
        _now = now_cn()
        _last_ts = float(_last_map.get(code, 0.0) or 0.0)
        if (not _is_web_runtime()) and (not _collector_running()) and is_cn_trading_time(_now) and ((_now.timestamp() - _last_ts) >= max(30, int(_refresh_sec))):
            record_intraday_point(target=code, estimate=est, date_str=_now.date().isoformat())
            _last_map[code] = _now.timestamp()
            st.session_state["detail_last_sample_ts"] = _last_map

    st.divider()

    # --- intraday curve ---
    st.subheader("盘中估值曲线（intraday）")
    max_points = 200
    intraday = intraday_load_fund_series(code, limit=800)
    intraday = _filter_trading_session(intraday)
    intraday = _downsample(intraday, max_points)

    if not intraday:
        st.info("暂无盘中序列")
    else:
        df = pd.DataFrame(intraday)

        # 兜底字段
        if "t" not in df:
            df["t"] = ""
        if "est_nav" not in df:
            df["est_nav"] = None
        if "marker" not in df:
            df["marker"] = None

        fig = go.Figure()

        # 1️⃣ 盘中估算折线
        fig.add_trace(
            go.Scatter(
                x=df["t"],
                y=df["est_nav"],
                mode="lines+markers",
                name="盘中估算净值",
                line=dict(width=2),
                marker=dict(size=5),
                hovertemplate=(
                    "时间: %{x}<br>"
                    "估算净值: %{y}<br>"
                    "<extra></extra>"
                ),
            )
        )

        # 2️⃣ 收盘标记点（marker=CLOSE）
        close_df = df[df["marker"] == "CLOSE"]
        if not close_df.empty:
            for _, row in close_df.iterrows():
                fig.add_vline(
                    x=row["t"],
                    line=dict(color="red", width=2, dash="dash"),
                    annotation_text="收盘",
                    annotation_position="top",
                )

        fig.update_layout(
            height=360,
            margin=dict(l=40, r=20, t=30, b=40),
            xaxis_title="时间",
            yaxis_title="净值",
            hovermode="x unified",
        )

        st.plotly_chart(fig, use_container_width=True)

        # 最近一条点的摘要
        last = intraday[-1]
        st.caption(
            f"最近：t={last.get('t')} | "
            f"est_nav={last.get('est_nav')} | "
            f"method={last.get('method')} | "
            f"conf={last.get('confidence')} | "
            f"marker={last.get('marker')}"
        )

    st.divider()

    st.subheader("估算缩水解释（估算收盘 vs 官方净值）")

    summary = fund_gap_summary(code, days_back=120)
    threshold = st.slider("异常阈值（绝对误差%）", min_value=0.10, max_value=2.00, value=0.30, step=0.05)

    if summary["count"] == 0:
        st.info("暂无已结算（settled）的历史对比数据。等至少有 1 天官方净值后，这里会自动出现误差分析。")
    else:
        latest = summary["latest"]

        abs_gap = float(latest["abs_gap_pct"])
        if abs_gap > threshold:
            st.warning(f"⚠️ 该基金最近已结算日误差 {abs_gap:.4f}% > 阈值 {threshold:.2f}%：盘中收益/涨跌幅可能偏“虚胖”，建议谨慎参考尾盘预估。")
        else:
            st.success(f"✅ 最近误差 {abs_gap:.4f}% ≤ 阈值 {threshold:.2f}%：估算口径较稳定。")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("样本天数", f'{summary["count"]}')
        c2.metric("平均绝对误差", f'{summary["mae_pct"]:.4f}%')
        c3.metric("最大绝对误差", f'{summary["max_abs_gap_pct"]:.4f}%')
        c4.metric("命中率(≤0.30%)", f'{summary["hit_rate_pct"]:.1f}%')

        st.markdown(
            f"""
    **最新已结算日：{latest["date"]}**  
    - 估算收盘净值：`{latest["estimated_nav_close"]:.6f}`  
    - 官方净值：`{latest["official_nav"]:.6f}`  
    - 误差：`{latest["gap_nav"]:+.6f}`（`{latest["gap_pct"]:+.4f}%`）
    """
        )

        reasons = guess_gap_reasons(code, float(latest["abs_gap_pct"]))
        with st.expander("为什么会缩水/放大？（规则解释）", expanded=True):
            for r in reasons:
                st.write("- " + r)


    st.subheader("误差历史（已结算日）")

    gap_rows = fund_gap_table(code, days_back=120)
    if not gap_rows:
        st.info("暂无误差历史（需要 settled 数据）。")
    else:
        df_gap = pd.DataFrame(gap_rows)

        # 表格：最近 N 天
        total_n = len(df_gap)

        # slider 边界保护
        if total_n <= 1:
            show_n = total_n
        else:
            min_n = 2
            max_n = min(60, total_n)
            default_n = min(20, max_n)

            show_n = st.slider(
                "展示最近 N 个已结算日",
                min_value=min_n,
                max_value=max_n,
                value=default_n,
                step=1,
            )

        df_show = df_gap.tail(show_n).copy()

        # 误差曲线：abs_gap_pct
        fig_gap = go.Figure()
        fig_gap.add_trace(
            go.Scatter(
                x=df_show["date"],
                y=df_show["abs_gap_pct"],
                mode="lines+markers",
                name="绝对误差(%)",
                hovertemplate="日期: %{x}<br>绝对误差: %{y:.4f}%<extra></extra>",
            )
        )
        # 加一条阈值参考线：0.30%
        fig_gap.add_hline(
            y=0.30,
            line=dict(color="gray", dash="dot"),
            annotation_text="0.30% 阈值",
            annotation_position="top left",
        )
        fig_gap.update_layout(
            height=300,
            margin=dict(l=40, r=20, t=30, b=40),
            xaxis_title="日期",
            yaxis_title="绝对误差(%)",
        )
        st.plotly_chart(fig_gap, use_container_width=True)

        # 表格展示
        st.dataframe(
            df_show[["date", "estimated_nav_close", "official_nav", "gap_nav", "gap_pct", "abs_gap_pct"]],
            width="stretch",
            hide_index=True,
        )
        st.caption("说明：这里统计的是“估算收盘净值 vs 官方净值”的误差，仅包含 settle_status=settled 的日期。")


    # --- history nav ---
    st.subheader("历史净值（official/estimated）")
    days = st.slider("展示最近 N 天", min_value=10, max_value=240, value=60, step=10)
    hist = fund_history(code, days_back=days)
    if not hist:
        st.info("暂无历史净值：需要历史源或日结数据累计。")
    else:
        st.dataframe(hist, width="stretch", hide_index=True)

    st.divider()

    # --- coverage status explanation ---
    st.subheader("当天收益覆盖状态（estimated_only vs settled）")
    d = st.date_input("选择日期查看覆盖状态", value=now_cn().date())
    ds = d.isoformat()
    row = _read_ledger_status(code, ds)

    if not row:
        st.info("该日期在 daily_ledger 中没有记录。你可以去 Ledger 页生成 estimated close，再尝试结算。")
    else:
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

        if status == "estimated_only":
            st.warning(
                "说明：该日收益仍是【估算】。夜间官方净值发布后，可能出现“缩水/扩大”的差异。\n\n"
                "常见原因：\n"
                "- 盘中估值依赖持仓股票实时涨跌（估算）\n"
                "- 收盘后基金会计按收盘价计算并发布官方净值（真实）\n"
                "- 部分基金/ETF 还有折溢价、汇率/期货、T+1 数据延迟等影响"
            )
        elif status == "settled":
            st.success("说明：该日收益已被官方净值覆盖（settled），可作为真实历史收益统计口径。")


render()
