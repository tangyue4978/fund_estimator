from __future__ import annotations

from datetime import date
from typing import Optional, Dict

from services.snapshot_service import build_positions_as_of
from services.adjustment_service import add_adjustment, remove_adjustments_by_code_date, list_adjustments


def _get_snapshot_map(d: str) -> Dict[str, dict]:
    snaps = build_positions_as_of(d)
    mp = {}
    for s in snaps:
        mp[s.code] = {
            "shares_end": float(s.shares_end),
            "avg_cost_nav_end": float(s.avg_cost_nav_end),
            "realized_pnl_end": float(s.realized_pnl_end),
        }
    return mp


def _is_ui_edit_item(it: dict) -> bool:
    src = str(it.get("source", "") or "").strip().lower()
    if src == "ui_edit":
        return True
    note = str(it.get("note", "") or "").strip().lower()
    if not note:
        return False
    return (
        "[ui_edit]" in note
        or note.startswith("edit->")
        or note.startswith("ui_edit")
        or note.startswith("ui edit")
        or (note.startswith("ui") and ("edit" in note or "\u7f16\u8f91" in note))
    )


def apply_position_edit(
    *,
    effective_date: str,
    code: str,
    shares_end: float,
    avg_cost_nav_end: float,
    realized_pnl_end: float = 0.0,
    note: Optional[str] = None,
) -> None:
    """
    Convert edited end-of-day position target into adjustment rows.
    Overwrite only same-day UI-edit rows (source=ui_edit), keeping manual rows intact.

    MVP behavior:
    - BUY/SELL price uses avg_cost_nav_end from input
    - realized delta is aligned by CASH_ADJ
    """
    code = (code or "").strip()
    if not code:
        raise ValueError("code is required")

    if shares_end < 0:
        raise ValueError("shares_end must be >= 0")
    if avg_cost_nav_end < 0:
        raise ValueError("avg_cost_nav_end must be >= 0")

    _ = date.fromisoformat(effective_date)  # validate date format

    # Keep old UI-edit rows for rollback if write fails midway.
    old_ui_items = [
        it
        for it in list_adjustments(code)
        if str(it.get("effective_date", "")).strip() == effective_date and _is_ui_edit_item(it)
    ]

    try:
        # Overwrite mode for UI-edit rows of the day.
        remove_adjustments_by_code_date(code, effective_date, source="ui_edit")

        base_map = _get_snapshot_map(effective_date)
        base_pos = base_map.get(code, {"shares_end": 0.0, "avg_cost_nav_end": 0.0, "realized_pnl_end": 0.0})
        base_sh = float(base_pos["shares_end"])

        # 1) Shares delta -> BUY/SELL
        delta_sh = float(shares_end) - base_sh
        if abs(delta_sh) > 1e-9:
            if delta_sh > 0:
                add_adjustment(
                    type="BUY",
                    code=code,
                    effective_date=effective_date,
                    shares=delta_sh,
                    price=float(avg_cost_nav_end) if avg_cost_nav_end > 0 else 1.0,
                    note=note or "edit->BUY",
                    source="ui_edit",
                )
            else:
                add_adjustment(
                    type="SELL",
                    code=code,
                    effective_date=effective_date,
                    shares=abs(delta_sh),
                    price=float(avg_cost_nav_end) if avg_cost_nav_end > 0 else 1.0,
                    note=note or "edit->SELL",
                    source="ui_edit",
                )

        # 2) realized delta -> CASH_ADJ
        # SELL may create realized pnl by itself; CASH_ADJ aligns final target.
        cur_map = _get_snapshot_map(effective_date)
        cur_real_after = float(cur_map.get(code, {}).get("realized_pnl_end", 0.0))

        target_real = float(realized_pnl_end)
        delta_real = target_real - cur_real_after
        if abs(delta_real) > 1e-6:
            add_adjustment(
                type="CASH_ADJ",
                code=code,
                effective_date=effective_date,
                cash=delta_real,
                note=note or "edit->CASH_ADJ",
                source="ui_edit",
            )
    except Exception:
        # Best-effort rollback for partial write.
        try:
            remove_adjustments_by_code_date(code, effective_date, source="ui_edit")
            for it in old_ui_items:
                t = str(it.get("type", "")).strip().upper()
                if t not in ("BUY", "SELL", "CASH_ADJ"):
                    continue
                add_adjustment(
                    type=t,
                    code=code,
                    effective_date=effective_date,
                    shares=float(it.get("shares", 0.0) or 0.0),
                    price=float(it.get("price", 0.0) or 0.0),
                    cash=float(it.get("cash", 0.0) or 0.0),
                    note=it.get("note"),
                    source="ui_edit",
                )
        finally:
            raise
