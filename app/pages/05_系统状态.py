import sys
import time
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

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
)
from config import constants
from services import supabase_client
from services.auth_guard import require_login
from services.cloud_status_service import get_cloud_error
from services.estimation_service import estimate_one
from services.watchlist_service import watchlist_list
from storage import paths


configure_page("系统状态", icon="🩺")
apply_app_style()
require_login(render_sidebar=False)


def _mask_secret(value: str) -> str:
    text = str(value or "").strip()
    return "已配置" if text else "未配置"


def _safe_cloud_error(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.split("（原始信息：", 1)[0].strip()


def _check_table(table: str, select: str) -> dict:
    start = time.perf_counter()
    try:
        rows = supabase_client.get_rows(
            table,
            params={
                "user_id": f"eq.{paths.current_user_id()}",
                "select": select,
                "limit": "1",
            },
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {
            "检查项": table,
            "状态": "正常",
            "耗时(ms)": elapsed_ms,
            "说明": f"可读取，返回 {len(rows)} 条样本",
        }
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {
            "检查项": table,
            "状态": "异常",
            "耗时(ms)": elapsed_ms,
            "说明": f"访问失败（{type(e).__name__}）",
        }


def _render_cloud_errors() -> None:
    section_header("最近云端异常", "显示各数据模块在本次浏览器会话中最近记录到的访问状态。")
    scopes = [
        ("watchlist", "自选基金"),
        ("adjustments", "持仓流水"),
        ("daily_ledger", "日结台账"),
        ("portfolio_ledger", "组合历史账本"),
    ]
    rows = []
    for scope, name in scopes:
        err = _safe_cloud_error(get_cloud_error(scope))
        rows.append({"模块": name, "状态": "异常" if err else "正常", "信息": err or ""})
    if all(row["状态"] == "正常" for row in rows):
        st.success("当前会话未记录到云端数据异常。")
    st.dataframe(
        rows,
        width="stretch",
        hide_index=True,
        height=dataframe_height(len(rows), min_rows=4, max_rows=6),
        column_config={
            "模块": st.column_config.TextColumn(width="medium"),
            "状态": st.column_config.TextColumn(width="small"),
            "信息": st.column_config.TextColumn(width="large"),
        },
    )
    st.session_state["system_status_cloud_errors"] = rows


def _build_report(*, url: str, key: str, enabled: bool, test_code: str) -> dict:
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "user_scope": "authenticated",
        "supabase": {
            "enabled": enabled,
            "url": "configured" if url else "missing",
            "key": "configured" if key else "missing",
        },
        "cloud_checks": st.session_state.get("system_status_cloud_checks", []),
        "cloud_errors": st.session_state.get("system_status_cloud_errors", []),
        "estimate_check": st.session_state.get("system_status_estimate_check", {}),
        "test_code": test_code,
    }


def render() -> None:
    page_header(
        "系统状态",
        "检查云端存储、数据模块和行情估值的可用性，并下载脱敏诊断报告。",
        eyebrow="诊断中心",
    )

    url, key = supabase_client.get_config()
    enabled = supabase_client.is_enabled()
    c1, c2, c3 = st.columns(3)
    c1.metric("登录状态", "已认证")
    c2.metric("云端存储", "已配置" if enabled else "未配置")
    c3.metric("数据隔离", "按登录用户")

    with st.expander("配置概览", expanded=True):
        st.write(
            {
                "SUPABASE_URL": "已配置" if url else "未配置",
                "SUPABASE_KEY": _mask_secret(key),
            }
        )

    section_header("云端数据访问检查", "验证必要数据表、字段和当前登录用户的读取权限。")
    if not enabled:
        degraded_notice(
            "云端存储未配置，自选、持仓流水和日结台账将不可用。",
            "请配置 SUPABASE_URL 与 SUPABASE_KEY。",
            detail_label="所需配置",
        )
    else:
        if st.button("执行云端数据检查", type="primary", width="stretch"):
            checks = [
                _check_table("app_watchlist", "code"),
                _check_table("app_adjustments", "id,type,code,effective_date"),
                _check_table("app_daily_ledger", "date,code,settle_status"),
            ]
            st.session_state["system_status_cloud_checks"] = checks
        checks = st.session_state.get("system_status_cloud_checks", [])
        if checks:
            st.dataframe(
                checks,
                width="stretch",
                hide_index=True,
                height=dataframe_height(len(checks), min_rows=3, max_rows=6),
                column_config={
                    "检查项": st.column_config.TextColumn("数据表"),
                    "状态": st.column_config.TextColumn(width="small"),
                    "耗时(ms)": st.column_config.NumberColumn("耗时（毫秒）", format="%d"),
                    "说明": st.column_config.TextColumn(width="large"),
                },
            )
        else:
            empty_state("尚未执行云端检查", "点击上方按钮验证数据表、字段和读取权限。")

    _render_cloud_errors()

    section_header("行情估值检查", "使用一只基金验证行情源、估值方法和降级链路是否正常。")
    codes = watchlist_list()
    default_code = codes[0] if codes else "510300"
    code = st.text_input("测试基金代码", value=default_code, help="请输入 6 位基金代码。")
    code_text = code.strip()
    code_is_valid = code_text.isdigit() and len(code_text) == 6
    if code_text and not code_is_valid:
        st.error("基金代码应为 6 位数字。")
    if st.button(
        "执行行情估值检查",
        type="primary",
        width="stretch",
        disabled=not code_is_valid,
    ):
        try:
            start = time.perf_counter()
            est = estimate_one(code_text)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            estimate_payload = {
                "status": "正常",
                "elapsed_ms": elapsed_ms,
                "code": est.code,
                "name": est.name,
                "est_nav": est.est_nav,
                "est_change_pct": est.est_change_pct,
                "method": estimate_method_label(est.method),
                "confidence": est.confidence,
                "warning": est.warning,
                "est_time": est.est_time,
            }
            degraded = (
                est.method == constants.METHOD_FROZEN_NAV
                or float(est.confidence or 0.0) <= 0
            )
            if degraded:
                estimate_payload["status"] = "降级"
            st.session_state["system_status_estimate_check"] = estimate_payload
        except Exception as e:
            st.session_state["system_status_estimate_check"] = {
                "status": "异常",
                "code": code_text,
                "error": safe_exception_detail(e),
            }

    estimate_result = st.session_state.get("system_status_estimate_check", {})
    if isinstance(estimate_result, dict) and estimate_result:
        result_status = str(estimate_result.get("status", ""))
        if result_status == "正常":
            st.success(f"实时估值链路正常，耗时 {estimate_result.get('elapsed_ms', '—')} 毫秒。")
        elif result_status == "降级":
            degraded_notice(
                f"实时估值链路已降级，耗时 {estimate_result.get('elapsed_ms', '—')} 毫秒。",
                str(estimate_result.get("warning", "") or ""),
                detail_label="行情降级原因",
            )
        else:
            degraded_notice(
                "行情估值检查失败，请稍后重试。",
                str(estimate_result.get("error", "") or ""),
                detail_label="行情检查技术详情",
            )

        if result_status in {"正常", "降级"}:
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("基金", f"{estimate_result.get('code', '—')} · {estimate_result.get('name', '—')}")
            try:
                nav_text = f"{float(estimate_result.get('est_nav')):.6f}"
            except (TypeError, ValueError):
                nav_text = "—"
            try:
                change_text = f"{float(estimate_result.get('est_change_pct')):.2f}%"
            except (TypeError, ValueError):
                change_text = "—"
            try:
                confidence_text = f"{float(estimate_result.get('confidence')):.2f}"
            except (TypeError, ValueError):
                confidence_text = "—"
            r2.metric("预估净值", nav_text)
            r3.metric("预估涨跌幅", change_text)
            r4.metric("置信度", confidence_text)
            st.caption(
                f"估值方式：{estimate_result.get('method', '暂无')} · "
                f"估值时间：{estimate_result.get('est_time', '—')}"
            )

    st.divider()
    section_header("诊断报告", "报告会隐藏密钥内容，仅记录是否配置和本次检查结果。")
    report = _build_report(url=url, key=key, enabled=enabled, test_code=code.strip())
    st.download_button(
        "下载诊断报告 JSON",
        data=json.dumps(report, ensure_ascii=False, indent=2),
        file_name=f"fund_estimator_diagnostic_{time.strftime('%Y%m%d_%H%M%S')}.json",
        mime="application/json",
        width="stretch",
    )


render()
