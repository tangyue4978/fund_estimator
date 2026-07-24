from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from storage import paths
from services.trading_time import now_cn
from storage.json_store import ensure_json_file, update_json
from domain.estimate import EstimateResult


_MAX_POINTS_PER_TARGET = 720


def _today_str() -> str:
    # Use CN date to stay consistent with collector and trading session logic.
    return now_cn().date().isoformat()


def _now_hhmmss() -> str:
    # Use CN time so intraday points are in trading-session window.
    return now_cn().strftime("%H:%M:%S")


def _ensure_intraday_file(date_str: str, data: Optional[dict] = None) -> dict:
    """
    intraday 存储结构（按日期）：
    data/intraday/<date>.json
    {
      "date": "YYYY-MM-DD",
      "series": {
        "<target>": [ {point}, {point}, ... ],
        "portfolio": [ ... ],
      },
      "updated_at": "..."
    }
    """
    if not data or not isinstance(data, dict):
        data = {}

    if "date" not in data:
        data["date"] = date_str

    if "series" not in data or not isinstance(data.get("series"), dict):
        data["series"] = {}

    if "updated_at" not in data:
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")

    return data


def _build_point_from_estimate(estimate: EstimateResult) -> Dict[str, Any]:
    return {
        "est_nav": estimate.est_nav,
        "est_change_pct": estimate.est_change_pct,
        "method": estimate.method,
        "confidence": estimate.confidence,
        "warning": estimate.warning,
        "name": getattr(estimate, "name", None),
        "est_time": getattr(estimate, "est_time", None),
        "refresh_sec": getattr(estimate, "suggested_refresh_sec", None),
    }


def record_intraday_point(
    target: str,
    estimate: Optional[EstimateResult] = None,
    portfolio_view: Optional[dict] = None,
    *,
    date_str: Optional[str] = None,
    marker: Optional[str] = None,
) -> dict:
    """
    记录一个盘中点：
    - target: 基金代码 或 'portfolio'
    - estimate: 单只基金估值结果
    - portfolio_view: 组合视图（来自 portfolio_realtime_view_as_of）
    - marker: 可选标记，例如 "CLOSE"

    写入：data/intraday/<date>.json 的 series[target]
    点结构（常用字段）：
    {
      "t": "HH:MM:SS",
      "marker": "CLOSE" (可选),
      "est_nav": ...,
      "est_change_pct": ...,
      "method": ...,
      "confidence": ...,
      "warning": ...,
      "total_est_value": ... (portfolio),
      "total_est_pnl": ...,
      "total_est_pnl_pct": ...,
      "realtime_coverage_value_pct": ...
    }
    """
    target = (target or "").strip()
    if target != "portfolio" and not re.fullmatch(r"\d{6}", target):
        raise ValueError("target must be a 6-digit fund code or portfolio")
    if estimate is None and portfolio_view is None:
        raise ValueError("estimate or portfolio_view is required")

    d = str(date_str or _today_str()).strip()
    try:
        date.fromisoformat(d)
    except ValueError as exc:
        raise ValueError("date_str must be an ISO date") from exc
    p = paths.file_intraday(d)
    point_time = _now_hhmmss()
    point: Dict[str, Any] = {
        "t": point_time,
        "date": f"{d} {point_time}",
    }
    if marker:
        point["marker"] = str(marker)[:32]
    if estimate is not None:
        point.update(_build_point_from_estimate(estimate))
    if portfolio_view is not None:
        for key in (
            "total_est_value",
            "total_est_pnl",
            "total_est_pnl_pct",
            "realtime_coverage_value_pct",
        ):
            value = portfolio_view.get(key)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                numeric = 0.0
            point[key] = numeric if math.isfinite(numeric) else 0.0

    def updater(data: dict) -> dict:
        normalized = _ensure_intraday_file(d, data)
        series = normalized["series"]
        points = series.get(target, [])
        if not isinstance(points, list):
            points = []

        last = points[-1] if points and isinstance(points[-1], dict) else {}
        same_source_point = bool(
            estimate is not None
            and last
            and str(last.get("est_time") or "") == str(point.get("est_time") or "")
            and str(last.get("method") or "") == str(point.get("method") or "")
            and last.get("est_nav") == point.get("est_nav")
            and str(point.get("marker") or "") != "CLOSE"
        )
        if same_source_point:
            return normalized

        points.append(point)
        series[target] = points[-_MAX_POINTS_PER_TARGET:]
        normalized["series"] = series
        normalized["updated_at"] = now_cn().isoformat(timespec="seconds")
        return normalized

    updated = update_json(p, updater)
    points = updated.get("series", {}).get(target, []) if isinstance(updated, dict) else []
    return dict(points[-1]) if points and isinstance(points[-1], dict) else {}


def get_intraday_series(target: str, *, date_str: Optional[str] = None) -> List[dict]:
    """
    获取指定 target 的当日曲线点列表。
    """
    target = (target or "").strip()
    if target != "portfolio" and not re.fullmatch(r"\d{6}", target):
        raise ValueError("target must be a 6-digit fund code or portfolio")

    d = (date_str or _today_str()).strip()
    try:
        date.fromisoformat(d)
    except ValueError as exc:
        raise ValueError("date_str must be an ISO date") from exc
    p = paths.file_intraday(d)

    res = ensure_json_file(p)
    data = res.data if isinstance(res.data, dict) else {}
    data = _ensure_intraday_file(d, data)

    series = data.get("series", {})
    points = series.get(target, [])
    return points if isinstance(points, list) else []


def clear_intraday(*, date_str: Optional[str] = None) -> None:
    """
    清空指定日期的 intraday 数据（保留文件结构）。
    """
    d = (date_str or _today_str()).strip()
    p = paths.file_intraday(d)

    def updater(_: dict):
        data = _ensure_intraday_file(d, {})
        data["series"] = {}
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return data

    update_json(p, updater)


# =========================
# 标准导出：给 Fund Detail / UI 用
# =========================

def intraday_load_fund_series(code: str, limit: int = 240, *, date_str: Optional[str] = None) -> List[dict]:
    """
    返回单基金的盘中估值序列（来自 series[code]）。
    """
    code = (code or "").strip()
    if not code:
        return []
    pts = get_intraday_series(code, date_str=date_str)
    if isinstance(limit, int) and limit > 0:
        return pts[-limit:]
    return pts


def intraday_load_portfolio_series(limit: int = 240, *, date_str: Optional[str] = None) -> List[dict]:
    """
    返回组合的盘中序列（来自 series['portfolio']）。
    """
    pts = get_intraday_series("portfolio", date_str=date_str)
    if isinstance(limit, int) and limit > 0:
        return pts[-limit:]
    return pts


def intraday_has_close_marker(target: str, *, date_str: Optional[str] = None) -> bool:
    """
    判断某日 target 是否已存在 marker=CLOSE（避免重复写）
    """
    d = (date_str or _today_str()).strip()
    pts = get_intraday_series(target, date_str=d)
    # 只看最后 200 个点就够
    tail = pts[-200:] if len(pts) > 200 else pts
    for it in reversed(tail):
        if str(it.get("marker", "")) == "CLOSE":
            return True
    return False


def intraday_append_close_marker(
    target: str,
    *,
    estimate: Optional[EstimateResult] = None,
    portfolio_view: Optional[dict] = None,
    date_str: Optional[str] = None,
) -> dict:
    """
    写入收盘标记点（marker=CLOSE，t 固定 15:00:00 的语义由 UI 识别 marker 即可）
    注意：我们不强制把 t 写死为 15:00:00，因为线程调用时可能在 15:00:xx，
    UI 可以根据 marker 画线/标记。
    """
    d = (date_str or _today_str()).strip()
    if intraday_has_close_marker(target, date_str=d):
        return {}

    # 这里用 marker 让 UI 能标记“收盘点”
    return record_intraday_point(
        target=target,
        estimate=estimate,
        portfolio_view=portfolio_view,
        date_str=d,
        marker="CLOSE",
    )


# =========================
# 兼容别名（如果你其他地方还在用）
# =========================

def load_fund_series(code: str, limit: int = 240, *, date_str: Optional[str] = None) -> List[dict]:
    return intraday_load_fund_series(code, limit=limit, date_str=date_str)
