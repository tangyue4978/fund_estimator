from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Dict, List

import requests


def _load_secret(key: str) -> str:
    value = os.getenv(key, "").strip()
    if value:
        return value
    try:
        import streamlit as st  # type: ignore

        value = st.secrets.get(key, "")
        return str(value or "").strip()
    except Exception:
        return ""


def vision_config() -> Dict[str, str]:
    api_key = (
        _load_secret("GEMINI_API_KEY")
        or _load_secret("GOOGLE_API_KEY")
        or _load_secret("HOLDINGS_OCR_API_KEY")
    )
    base_url = (
        _load_secret("GEMINI_API_BASE_URL")
        or "https://generativelanguage.googleapis.com/v1beta"
    ).rstrip("/")
    model = (
        _load_secret("GEMINI_MODEL")
        or _load_secret("HOLDINGS_OCR_MODEL")
        or "gemini-2.5-flash-lite"
    ).strip()
    return {"api_key": api_key, "base_url": base_url, "model": model}


def is_vision_enabled() -> bool:
    cfg = vision_config()
    return bool(cfg["api_key"] and cfg["base_url"] and cfg["model"])


def _extract_json_block(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("empty response")
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.S | re.I)
    if fenced:
        return json.loads(fenced.group(1))

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return json.loads(raw[start : end + 1])
    raise ValueError("json object not found")


def _message_text(mode: str) -> str:
    if mode == "delta":
        return (
            "识别基金加减仓截图，只提取基金交易行，忽略标题、按钮、说明和合计。"
            "如果是买入，side 填 buy；如果是卖出或减仓，side 填 sell。"
            "优先提取 6 位基金代码。"
            "字段要求：rows[].code, rows[].fund_name, rows[].delta_shares, rows[].delta_amount, "
            "rows[].avg_price, rows[].side, rows[].confidence, rows[].notes。"
            "没有把握的字段填 null，不要猜。"
        )
    return (
        "识别基金当前持仓截图，只提取持仓列表中的基金行，忽略标题、按钮、说明和合计。"
        "优先提取 6 位基金代码。"
        "字段要求：rows[].code, rows[].fund_name, rows[].shares, rows[].avg_cost_nav, "
        "rows[].amount, rows[].cumulative_pnl, rows[].daily_pnl, rows[].pnl_pct, "
        "rows[].confidence, rows[].notes。"
        "如果同时出现总收益和日收益，cumulative_pnl 填总收益，daily_pnl 填日收益。"
        "没有把握的字段填 null，不要猜。"
    )


def _response_text(payload: Dict[str, Any]) -> str:
    candidates = payload.get("candidates")
    if isinstance(candidates, list) and candidates:
        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        if isinstance(parts, list):
            texts: List[str] = []
            for part in parts:
                if isinstance(part, dict) and part.get("text"):
                    texts.append(str(part.get("text")))
            return "\n".join(texts)
    return ""


def analyze_holdings_image(
    *,
    image_bytes: bytes,
    mime_type: str,
    filename: str,
    mode: str,
) -> Dict[str, Any]:
    cfg = vision_config()
    if not cfg["api_key"]:
        raise RuntimeError("未配置 Gemini API Key")

    encoded = base64.b64encode(image_bytes).decode("ascii")
    body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "你是基金持仓截图识别器。"
                            "你只能输出一个 JSON 对象，不要输出解释、markdown 或代码块。"
                            '顶层结构固定为 {"document_type":"","summary":"","rows":[],"warnings":[]}。'
                            f"文件名：{filename}。"
                            + _message_text(mode)
                        )
                    },
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": encoded,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    resp = requests.post(
        f"{cfg['base_url']}/models/{cfg['model']}:generateContent",
        headers={
            "x-goog-api-key": cfg["api_key"],
            "Content-Type": "application/json",
        },
        json=body,
        timeout=60,
    )
    resp.raise_for_status()
    payload = resp.json()
    text = _response_text(payload)
    data = _extract_json_block(text)
    if not isinstance(data, dict):
        raise ValueError("识别结果不是 JSON 对象")

    rows = data.get("rows")
    if not isinstance(rows, list):
        data["rows"] = []
    warnings = data.get("warnings")
    if not isinstance(warnings, list):
        data["warnings"] = []
    data.setdefault("document_type", "")
    data.setdefault("summary", "")
    return data
