from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from storage import paths
from storage.json_store import ensure_json_file, update_json
from domain.estimate import EstimateResult


def _today_str() -> str:
    return date.today().isoformat()


def _now_hhmmss() -> str:
    return datetime.now().strftime("%H:%M:%S")


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
        "refresh_sec": getattr(estimate, "refresh_sec", None),
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
    if not target:
        raise ValueError("target is required")

    d = (date_str or _today_str()).strip()
    p = paths.file_intraday(d)

    def updater(data: dict):
        data = _ensure_intraday_file(d, data)
        series = data.get("series", {})
        points = series.get(target, [])
        if not isinstance(points, list):
            points = []

        point: Dict[str, Any] = {"t": _now_hhmmss()}
        if marker:
            point["marker"] = marker

        if estimate is not None:
            point.update(_build_point_from_estimate(estimate))

        if portfolio_view is not None:
            point.update(
                {
                    "total_est_value": float(portfolio_view.get("total_est_value", 0.0) or 0.0),
                    "total_est_pnl": float(portfolio_view.get("total_est_pnl", 0.0) or 0.0),
                    "total_est_pnl_pct": float(portfolio_view.get("total_est_pnl_pct", 0.0) or 0.0),
                    "realtime_coverage_value_pct": float(portfolio_view.get("realtime_coverage_value_pct", 0.0) or 0.0),
                }
            )

        points.append(point)
        series[target] = points
        data["series"] = series
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return data

    return update_json(p, updater)


def get_intraday_series(target: str, *, date_str: Optional[str] = None) -> List[dict]:
    """
    获取指定 target 的当日曲线点列表。
    """
    target = (target or "").strip()
    if not target:
        raise ValueError("target is required")

    d = (date_str or _today_str()).strip()
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
