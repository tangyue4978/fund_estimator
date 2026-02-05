from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, date
from typing import List, Dict, Optional
import json
from pathlib import Path
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


def _now() -> datetime:
    if ZoneInfo is None:
        return datetime.now()
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _today_str() -> str:
    return _now().date().isoformat()


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


def _minutes_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


from storage import paths
paths.ensure_dirs()
_LOG_PATH = paths.file_collector_log()
_STATUS_PATH = paths.file_collector_status()


def _print(msg: str) -> None:
    ts = _now().strftime("%Y-%m-%d %H:%M:%S")
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
        payload["updated_at"] = _now().isoformat(timespec="seconds")
        _STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass



def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fund intraday collector (background).")
    parser.add_argument("--interval", type=int, default=30, help="sampling interval in seconds")
    parser.add_argument("--only-trading", action="store_true", help="sample only during trading session")
    parser.add_argument("--close-window-min", type=int, default=2, help="close marker window in minutes")
    parser.add_argument("--settle-hour", type=int, default=20, help="daily auto-settle start hour (0-23)")
    parser.add_argument("--settle-deadline-hour", type=int, default=24, help="daily auto-settle deadline hour (1-24)")
    parser.add_argument("--settle-retry-min", type=int, default=30, help="retry interval in minutes when settle gets 0 rows")
    parser.add_argument("--settle-days-back", type=int, default=7, help="days_back for settle_pending_days")
    parser.add_argument("--once", action="store_true", help="run once and exit")
    parser.add_argument("--codes", type=str, default="", help="comma-separated codes; empty means watchlist")
    args = parser.parse_args(argv)

    # 延迟 import（避免脚本启动时因依赖加载失败导致无提示退出）
    try:
        from services.watchlist_service import watchlist_list
        from services.estimation_service import estimate_many
        from services.intraday_service import record_intraday_point, intraday_append_close_marker
        from services.settlement_service import count_pending_settlement, settle_pending_days
    except Exception as e:
        _print(f"IMPORT ERROR: {e}")
        return 2

    interval = max(30, int(args.interval))
    only_trading = bool(args.only_trading)
    settle_hour = max(0, min(23, int(args.settle_hour)))
    settle_deadline_hour = max(settle_hour + 1, min(24, int(args.settle_deadline_hour)))
    settle_start_min = settle_hour * 60
    settle_deadline_min = settle_deadline_hour * 60
    settle_retry_sec = max(60, int(args.settle_retry_min) * 60)
    settle_days_back = max(1, int(args.settle_days_back))
    last_settle_try_ts = 0.0

    _print(
        "started. "
        f"interval={interval}s only_trading={only_trading} close_window_min={args.close_window_min} "
        f"settle_hour={settle_hour} settle_deadline_hour={settle_deadline_hour} "
        f"settle_retry_min={int(settle_retry_sec/60)} settle_days_back={settle_days_back}"
    )

    while True:
        dt = _now()
        ds = _today_str()
        now_ts = time.time()

        # Evening auto settle:
        # - keep probing pending within settle window
        # - if pending > 0, run settle_pending_days
        minutes_now = _minutes_of_day(dt)
        in_settle_window = settle_start_min <= minutes_now < settle_deadline_min
        retry_due = (now_ts - last_settle_try_ts) >= settle_retry_sec
        if in_settle_window and retry_due:
            try:
                pending_count = count_pending_settlement(settle_days_back)
                last_settle_try_ts = now_ts
                if pending_count > 0:
                    _, settled_total = settle_pending_days(settle_days_back)
                    pending_after = count_pending_settlement(settle_days_back)
                    _print(
                        f"auto settle run: +{settled_total} rows, pending_before={pending_count}, pending_after={pending_after} "
                        f"(days_back={settle_days_back})"
                    )
                    pending_count = pending_after
                else:
                    _print(
                        f"auto settle probe: pending={pending_count}, "
                        f"will retry in {int(settle_retry_sec/60)} min"
                    )
                _write_status({
                    "running": True,
                    "date": ds,
                    "phase": "auto_settle",
                    "pending_count": int(pending_count),
                    "settle_hour": settle_hour,
                    "settle_deadline_hour": settle_deadline_hour,
                    "settle_retry_min": int(settle_retry_sec / 60),
                })
            except Exception as e:
                last_settle_try_ts = now_ts
                _print(f"auto settle error: {e}")
                _write_status({
                    "running": True,
                    "date": ds,
                    "phase": "error",
                    "last_error": f"auto_settle: {e}",
                })

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
