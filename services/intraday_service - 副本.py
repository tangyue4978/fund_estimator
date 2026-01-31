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


def _ensure_intraday_file(date_str: str) -> dict:
    p = paths.file_intraday(date_str)
    res = ensure_json_file(p)
    data = res.data if isinstance(res.data, dict) else {}
    if "date" not in data:
        data["date"] = date_str
    if "series" not in data or not isinstance(data.get("series"), dict):
        data["series"] = {}  # { target: [points...] }
    return data


def record_intraday_point(
    target: str,
    estimate: Optional[EstimateResult] = None,
    portfolio_view: Optional[dict] = None,
    *,
    date_str: Optional[str] = None,
) -> dict:
    """
    记录一个盘中点：
    - target: 基金代码或 'portfolio'
    - estimate: 单只基金估值结果
    - portfolio_view: 组合视图（来自 portfolio_realtime_view）
    写入: data/intraday/<date>.json

    点结构（最小集）：
    {
      "t": "HH:MM:SS",
      "est_nav": ...,
      "est_change_pct": ...,
      "method": ...,
      "confidence": ...,
      "warning": ...,
      "est_pnl": ...,
      "est_pnl_pct": ...,
      "realtime_coverage_value_pct": ...
    }
    """
    if not target:
        raise ValueError("target is required")

    d = date_str or _today_str()
    p = paths.file_intraday(d)

    def updater(data: dict):
        data = _ensure_intraday_file(d) if not data else data
        series = data.get("series", {})
        points = series.get(target, [])

        point: Dict[str, Any] = {"t": _now_hhmmss()}

        if estimate is not None:
            point.update(
                {
                    "est_nav": estimate.est_nav,
                    "est_change_pct": estimate.est_change_pct,
                    "method": estimate.method,
                    "confidence": estimate.confidence,
                    "warning": estimate.warning,
                }
            )

        if portfolio_view is not None:
            point.update(
                {
                    "total_est_value": portfolio_view.get("total_est_value", 0.0),
                    "total_est_pnl": portfolio_view.get("total_est_pnl", 0.0),
                    "total_est_pnl_pct": portfolio_view.get("total_est_pnl_pct", 0.0),
                    "realtime_coverage_value_pct": portfolio_view.get("realtime_coverage_value_pct", 0.0),
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
    if not target:
        raise ValueError("target is required")

    d = date_str or _today_str()
    p = paths.file_intraday(d)

    res = ensure_json_file(p)
    data = res.data if isinstance(res.data, dict) else {}
    series = data.get("series", {})
    points = series.get(target, [])
    return points if isinstance(points, list) else []


def clear_intraday(*, date_str: Optional[str] = None) -> None:
    """
    清空指定日期的 intraday 数据（保留文件结构）。
    """
    d = date_str or _today_str()
    p = paths.file_intraday(d)

    def updater(data: dict):
        data = _ensure_intraday_file(d)
        data["series"] = {}
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return data

    update_json(p, updater)


# =========================
# 标准导出（给 UI / Fund Detail 用）
# =========================

def intraday_load_fund_series(code: str, limit: int = 240):
    """
    返回单基金的盘中估值序列（给 Fund Detail 使用）
    统一返回 list[dict]，每个元素至少包含：
      - t
      - est_nav
      - est_change_pct
      - method
      - confidence
    """
    code = (code or "").strip()
    if not code:
        return []

    # 你原来 intraday_service 里，基本一定有类似的函数
    # 常见命名我都给你兜底了
    if "load_fund_series" in globals():
        return load_fund_series(code, limit=limit)

    if "load_series" in globals():
        return load_series(code, limit=limit)

    if "get_fund_series" in globals():
        return get_fund_series(code, limit=limit)

    # 最保底：直接读 intraday 存储文件
    try:
        from storage import paths
        from storage.json_store import load_json

        data = load_json(paths.file_intraday_fund(code), fallback={"items": []})
        items = data.get("items", [])
        if not isinstance(items, list):
            return []
        return items[-limit:]
    except Exception:
        return []

from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path

from storage.json_store import ensure_json_file, load_json, save_json
from storage import paths


def _now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _fund_intraday_path(code: str) -> str:
    """
    尽量用 paths.file_intraday_fund(code)，没有就退化到 data/intraday_fund_{code}.json
    """
    fn = getattr(paths, "file_intraday_fund", None)
    if callable(fn):
        return fn(code)
    # fallback
    data_dir = Path(getattr(paths, "DATA_DIR", Path("data")))
    return str(data_dir / f"intraday_fund_{code}.json")


from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path

from storage.json_store import ensure_json_file, load_json, save_json
from storage import paths


def _now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


from datetime import datetime, date
from typing import List, Dict, Any
from pathlib import Path

from storage.json_store import ensure_json_file, load_json, save_json
from storage import paths


def _now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _today_str() -> str:
    return date.today().isoformat()


def _fund_intraday_path(code: str) -> Path:
    """
    统一对齐到 storage.paths 里你真实存在的函数：file_intraday(code)
    你刚才验证过它会返回：data/intraday/510300.json
    """
    # 优先使用项目已有的 file_intraday
    fn = getattr(paths, "file_intraday", None)
    if callable(fn):
        p = fn(code)
        return p if isinstance(p, Path) else Path(p)

    # 兼容：如果未来有 file_intraday_fund，再用它
    fn2 = getattr(paths, "file_intraday_fund", None)
    if callable(fn2):
        p = fn2(code)
        return p if isinstance(p, Path) else Path(p)

    # fallback：data/intraday/{code}.json
    data_dir = getattr(paths, "DATA_DIR", None)
    if data_dir is None:
        data_dir = Path("data")
    else:
        data_dir = data_dir if isinstance(data_dir, Path) else Path(data_dir)
    return data_dir / "intraday" / f"{code}.json"


def intraday_append_fund_point(code: str, point: Dict[str, Any], max_keep: int = 2000) -> None:
    """
    写入一条盘中点：
    - 自动补 date / t
    - 追加到 data/intraday_fund_{code}.json
    """
    code = (code or "").strip()
    if not code:
        return

    p = _fund_intraday_path(code)
    res = ensure_json_file(p)
    data = res.data if isinstance(res.data, dict) else {"items": []}
    items = data.get("items", [])
    if not isinstance(items, list):
        items = []

    row = dict(point)
    row.setdefault("date", _today_str())
    row.setdefault("t", _now_hms())

    items.append(row)
    if len(items) > max_keep:
        items = items[-max_keep:]

    data["items"] = items
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_json(p, data)


def intraday_load_fund_series(code: str, limit: int = 240) -> List[Dict[str, Any]]:
    code = (code or "").strip()
    if not code:
        return []
    p = _fund_intraday_path(code)
    data = load_json(p, fallback={"items": []})
    items = data.get("items", [])
    if not isinstance(items, list):
        return []
    return items[-limit:]


def intraday_has_close_marker(code: str, date_str: str) -> bool:
    """
    判断某天是否已写过 CLOSE 标记点（避免重复写）
    """
    code = (code or "").strip()
    if not code:
        return False
    p = _fund_intraday_path(code)
    data = load_json(p, fallback={"items": []})
    items = data.get("items", [])
    if not isinstance(items, list) or not items:
        return False

    # 只扫最后一段，提高性能
    tail = items[-200:]
    for it in reversed(tail):
        if str(it.get("date", "")) != date_str:
            continue
        if str(it.get("marker", "")) == "CLOSE":
            return True
    return False


def intraday_append_close_marker(code: str, base_point: Dict[str, Any] | None = None, date_str: str | None = None) -> None:
    """
    写入 15:00 CLOSE 标记点（可带上当时估值字段）
    """
    code = (code or "").strip()
    if not code:
        return
    ds = date_str or _today_str()
    if intraday_has_close_marker(code, ds):
        return

    row = dict(base_point or {})
    row["marker"] = "CLOSE"
    row["date"] = ds
    row["t"] = "15:00:00"
    intraday_append_fund_point(code, row)
