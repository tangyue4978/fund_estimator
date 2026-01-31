from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, date
from typing import List, Dict, Optional
import json
from pathlib import Path


def _now() -> datetime:
    return datetime.now()


def _today_str() -> str:
    return date.today().isoformat()


def _is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5


def _is_cn_trading_time(dt: datetime) -> bool:
    """
    简化版A股交易时段：
    - 周一到周五
    - 09:30-11:30, 13:00-15:00
    """
    if not _is_weekday(dt):
        return False
    hm = dt.hour * 60 + dt.minute
    # 09:30=570, 11:30=690, 13:00=780, 15:00=900
    return (570 <= hm <= 690) or (780 <= hm <= 900)


def _is_close_window(dt: datetime, minutes: int = 2) -> bool:
    """
    收盘窗口：默认 15:00 附近 +/- minutes
    例如 minutes=2：14:58~15:02
    """
    if not _is_weekday(dt):
        return False
    hm = dt.hour * 60 + dt.minute
    close_hm = 15 * 60  # 15:00
    return abs(hm - close_hm) <= minutes


from storage import paths
paths.ensure_dirs()
_LOG_PATH = paths.file_collector_log()
_STATUS_PATH = paths.file_collector_status()


def _print(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[collector] {ts} {msg}"
    print(line, flush=True)

    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _write_status(payload: dict) -> None:
    """
    写心跳/状态到 storage/status/collector_status.json
    """
    try:
        _STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(payload)
        payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass



def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fund intraday collector (background).")
    parser.add_argument("--interval", type=int, default=10, help="采样间隔秒（默认10）")
    parser.add_argument("--only-trading", action="store_true", help="仅交易时段采样")
    parser.add_argument("--close-window-min", type=int, default=2, help="收盘窗口分钟（默认2）")
    parser.add_argument("--once", action="store_true", help="只跑一轮就退出（调试用）")
    parser.add_argument("--codes", type=str, default="", help="指定代码（逗号分隔），不填则用 watchlist")
    args = parser.parse_args(argv)

    # 延迟 import（避免脚本启动时因依赖加载失败导致无提示退出）
    try:
        from services.watchlist_service import watchlist_list
        from services.estimation_service import estimate_many
        from services.intraday_service import record_intraday_point, intraday_append_close_marker
    except Exception as e:
        _print(f"IMPORT ERROR: {e}")
        return 2

    interval = max(3, int(args.interval))
    only_trading = bool(args.only_trading)

    _print(f"started. interval={interval}s only_trading={only_trading} close_window_min={args.close_window_min}")

    while True:
        dt = _now()
        ds = _today_str()

        _write_status({
            "running": True,
            "date": ds,
            "phase": "loop",
            "interval_sec": interval,
            "only_trading": only_trading,
        })

        # codes 来源：--codes 优先，否则 watchlist
        if args.codes.strip():
            codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        else:
            try:
                codes = watchlist_list()
            except Exception as e:
                _print(f"watchlist_list error: {e}")
                codes = []

        # 没有 codes：直接睡眠
        if not codes:
            _print("no codes. sleep...")

            _write_status({
                "running": True,
                "date": ds,
                "phase": "no_codes",
                "codes_count": 0,
            })
            if args.once:
                return 0
            time.sleep(interval)
            continue

        # 交易时段控制：only_trading=true 且不在交易时段 => 只做“收盘标记窗口”，其他不做
        in_trading = _is_cn_trading_time(dt)
        in_close_window = _is_close_window(dt, minutes=int(args.close_window_min))

        if only_trading and (not in_trading) and (not in_close_window):
            _print("outside trading time. sleep...")
            _write_status({
                "running": True,
                "date": ds,
                "phase": "outside_trading",
                "codes_count": len(codes),
            })
            if args.once:
                return 0
            time.sleep(interval)
            continue

        # 拉取估值（批量）
        try:
            est_map: Dict[str, object] = estimate_many(codes)
            _write_status({
                "running": True,
                "date": ds,
                "phase": "estimated",
                "codes_count": len(codes),
            })
        except Exception as e:
            _print(f"estimate_many error: {e}")
            _write_status({
                "running": True,
                "date": ds,
                "phase": "error",
                "last_error": f"estimate_many: {e}",
            })
            if args.once:
                return 1
            time.sleep(interval)
            continue

        # 收盘点：窗口内写一次（intraday_append_close_marker 内部会去重）
        if in_close_window:
            try:
                for c in codes:
                    est = est_map.get(c)
                    intraday_append_close_marker(target=c, estimate=est, date_str=ds)
                _print(f"close marker ensured for {len(codes)} codes (date={ds})")
            except Exception as e:
                _print(f"append_close_marker error: {e}")
                _write_status({
                    "running": True,
                    "date": ds,
                    "phase": "error",
                    "last_error": f"append_close_marker: {e}",
                })

        # 普通盘中点
        if (not only_trading) or in_trading:
            ok = 0
            try:
                for c in codes:
                    est = est_map.get(c)
                    if not est:
                        continue
                    record_intraday_point(target=c, estimate=est, date_str=ds)
                    ok += 1
                _print(f"sampled {ok}/{len(codes)} codes (date={ds})")
                _write_status({
                    "running": True,
                    "date": ds,
                    "phase": "sampled",
                    "codes_count": len(codes),
                    "sampled_ok": ok,
                })
            except Exception as e:
                _print(f"record_intraday_point error: {e}")
                _write_status({
                    "running": True,
                    "date": ds,
                    "phase": "error",
                    "last_error": f"record_intraday_point: {e}",
                })

        if args.once:
            return 0

        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
