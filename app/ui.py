from __future__ import annotations

from html import escape

import streamlit as st


ESTIMATE_METHOD_LABELS = {
    "OFFICIAL_GSZ": "官方盘中估值",
    "ETF_IIV": "ETF 实时参考净值",
    "INDEX_PROXY": "指数代理估值",
    "HOLDING_WEIGHTED": "持仓加权估值",
    "FROZEN_NAV": "最近官方净值（降级）",
    "OFFICIAL_CLOSE": "官方收盘净值",
    "ESTIMATED_CLOSE": "收盘估算",
    "MOCK": "模拟数据",
    "N/A": "暂无",
}

SETTLEMENT_STATUS_LABELS = {
    "settled": "已按官方净值结算",
    "estimated_only": "仅有收盘估算",
}


def configure_page(title: str, *, icon: str = "📈") -> None:
    st.set_page_config(
        page_title=f"{title} · 基金估值",
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="auto",
    )


def apply_app_style() -> None:
    """Apply presentation-only defaults shared by every Streamlit page."""
    st.markdown(
        """
<style>
    :root {
        --app-border: color-mix(in srgb, currentColor 16%, transparent);
        --app-muted: color-mix(in srgb, currentColor 68%, transparent);
        --app-danger: #b42318;
        --app-danger-hover: #912018;
    }

    [data-testid="stAppViewContainer"] .block-container {
        max-width: 1440px;
        padding-top: 1.75rem;
        padding-bottom: 4rem;
    }

    [data-testid="stAppViewContainer"] h1 {
        font-size: clamp(1.75rem, 3vw, 2.4rem);
        line-height: 1.2;
        letter-spacing: -0.025em;
        margin-bottom: 0.35rem;
    }

    [data-testid="stAppViewContainer"] h2,
    [data-testid="stAppViewContainer"] h3 {
        scroll-margin-top: 1rem;
    }

    .app-page-intro {
        color: var(--app-muted);
        font-size: 1rem;
        line-height: 1.65;
        margin: 0 0 1.35rem;
        max-width: 78ch;
    }

    [data-testid="stMetric"] {
        border: 1px solid var(--app-border);
        border-radius: 0.75rem;
        padding: 0.8rem 0.95rem;
        min-height: 6rem;
    }

    [data-testid="stDataFrame"],
    [data-testid="stDataEditor"] {
        border: 1px solid var(--app-border);
        border-radius: 0.7rem;
        overflow: hidden;
    }

    .stButton > button,
    .stDownloadButton > button,
    [data-testid="stFormSubmitButton"] > button {
        min-height: 2.75rem;
        white-space: normal;
        line-height: 1.25;
    }

    button:focus-visible,
    input:focus-visible,
    textarea:focus-visible,
    [role="radio"]:focus-visible,
    [role="checkbox"]:focus-visible,
    [role="combobox"]:focus-visible {
        outline: 3px solid #1570ef !important;
        outline-offset: 2px !important;
    }

    [class*="st-key-danger_"] {
        border-color: color-mix(in srgb, var(--app-danger) 36%, transparent) !important;
        background: color-mix(in srgb, var(--app-danger) 5%, transparent);
    }

    [class*="st-key-danger_"] .stButton > button,
    [class*="st-key-danger_"] button[kind="primary"] {
        background: var(--app-danger);
        border-color: var(--app-danger);
        color: white;
    }

    [class*="st-key-danger_"] .stButton > button:hover,
    [class*="st-key-danger_"] button[kind="primary"]:hover {
        background: var(--app-danger-hover);
        border-color: var(--app-danger-hover);
    }

    [role="radiogroup"] {
        flex-wrap: wrap;
        row-gap: 0.35rem;
    }

    @media (max-width: 720px) {
        [data-testid="stAppViewContainer"] .block-container {
            padding: 3rem 0.8rem 3rem;
        }

        [data-testid="stHorizontalBlock"] {
            flex-wrap: wrap;
            gap: 0.75rem;
        }

        [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
            flex: 1 1 100% !important;
            width: 100% !important;
            min-width: 100% !important;
        }

        [data-testid="stMetric"] {
            min-height: auto;
        }

        .stButton > button,
        .stDownloadButton > button,
        [data-testid="stFormSubmitButton"] > button {
            width: 100%;
            min-height: 3rem;
        }
    }

    @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after {
            scroll-behavior: auto !important;
            animation-duration: 0.01ms !important;
            animation-iteration-count: 1 !important;
            transition-duration: 0.01ms !important;
        }
    }
</style>
""",
        unsafe_allow_html=True,
    )


def page_header(title: str, description: str, *, eyebrow: str = "") -> None:
    if eyebrow:
        st.caption(eyebrow)
    st.title(title)
    st.markdown(
        f'<p class="app-page-intro">{escape(description)}</p>',
        unsafe_allow_html=True,
    )


def section_header(title: str, description: str = "") -> None:
    st.subheader(title)
    if description:
        st.caption(description)


def dataframe_height(
    row_count: int,
    *,
    min_rows: int = 2,
    max_rows: int = 12,
    row_height: int = 35,
) -> int:
    visible_rows = max(min_rows, min(max_rows, max(0, int(row_count))))
    return 40 + visible_rows * row_height


def empty_state(title: str, description: str) -> None:
    st.info(f"**{title}**\n\n{description}")


def degraded_notice(summary: str, detail: str = "", *, detail_label: str = "查看技术详情") -> None:
    st.warning(summary)
    detail_text = str(detail or "").strip()
    if detail_text:
        with st.expander(detail_label, expanded=False):
            st.code(detail_text, language=None)


def safe_exception_detail(error: BaseException) -> str:
    """Return useful diagnostics without exposing request URLs or identifiers."""
    error_name = type(error).__name__ or "Error"
    if isinstance(error, ValueError):
        message = str(error or "").strip()
        if message and len(message) <= 240 and "http://" not in message.lower() and "https://" not in message.lower():
            return f"{error_name}: {message}"
    return f"{error_name}：详细请求信息已隐藏，请查看受控的服务端日志。"


def action_error(summary: str, error: BaseException) -> None:
    st.error(summary)
    st.caption(safe_exception_detail(error))


def danger_container(key: str):
    safe_key = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(key))
    return st.container(key=f"danger_{safe_key}", border=True)


def estimate_method_label(value: object) -> str:
    raw = str(value or "").strip()
    return ESTIMATE_METHOD_LABELS.get(raw, raw or "暂无")


def settlement_status_label(value: object) -> str:
    raw = str(value or "").strip()
    return SETTLEMENT_STATUS_LABELS.get(raw, raw or "未知")
