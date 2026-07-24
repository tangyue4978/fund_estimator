from __future__ import annotations

import math
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from services import adjustment_service, edit_bridge_service, supabase_client


class PositionEditRpcTests(unittest.TestCase):
    def test_supabase_rpc_uses_postgrest_rpc_path(self) -> None:
        post = Mock(return_value=SimpleNamespace(status_code=200))
        with (
            patch.object(supabase_client, "get_config", return_value=("https://project.supabase.co", "key")),
            patch.object(supabase_client, "_SESSION", SimpleNamespace(post=post)),
        ):
            supabase_client.call_rpc("app_apply_position_edit", {"p_code": "000001"})

        self.assertEqual(
            post.call_args.args[0],
            "https://project.supabase.co/rest/v1/rpc/app_apply_position_edit",
        )

    def test_rpc_receives_owner_and_target_and_clears_cache(self) -> None:
        response = SimpleNamespace(status_code=200)
        with (
            patch.object(adjustment_service.supabase_client, "is_enabled", return_value=True),
            patch.object(adjustment_service.paths, "current_user_id", return_value="u_random"),
            patch.object(adjustment_service.supabase_client, "call_rpc", return_value=response) as call_rpc,
            patch.object(adjustment_service, "_clear_adjustments_cache") as clear_cache,
        ):
            supported = adjustment_service.replace_ui_position_edit_atomic(
                effective_date="2026-07-24",
                code="000001",
                shares_end=12.5,
                avg_cost_nav_end=1.234,
                realized_pnl_end=5.0,
                note="rebalance",
            )

        self.assertTrue(supported)
        call_rpc.assert_called_once_with(
            "app_apply_position_edit",
            {
                "p_user_id": "u_random",
                "p_effective_date": "2026-07-24",
                "p_code": "000001",
                "p_shares_end": 12.5,
                "p_avg_cost_nav_end": 1.234,
                "p_realized_pnl_end": 5.0,
                "p_note": "rebalance",
            },
        )
        clear_cache.assert_called_once()

    def test_only_missing_rpc_allows_legacy_fallback(self) -> None:
        with (
            patch.object(adjustment_service.supabase_client, "is_enabled", return_value=True),
            patch.object(adjustment_service.paths, "current_user_id", return_value="u_random"),
            patch.object(
                adjustment_service.supabase_client,
                "call_rpc",
                return_value=SimpleNamespace(
                    status_code=404,
                    json=lambda: {"code": "PGRST202"},
                ),
            ),
        ):
            self.assertFalse(
                adjustment_service.replace_ui_position_edit_atomic(
                    effective_date="2026-07-24",
                    code="000001",
                    shares_end=1,
                    avg_cost_nav_end=1,
                    realized_pnl_end=0,
                )
            )

        with (
            patch.object(adjustment_service.supabase_client, "is_enabled", return_value=True),
            patch.object(adjustment_service.paths, "current_user_id", return_value="u_random"),
            patch.object(
                adjustment_service.supabase_client,
                "call_rpc",
                return_value=SimpleNamespace(status_code=500),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "atomic position edit failed"):
                adjustment_service.replace_ui_position_edit_atomic(
                    effective_date="2026-07-24",
                    code="000001",
                    shares_end=1,
                    avg_cost_nav_end=1,
                    realized_pnl_end=0,
                )

    def test_edit_bridge_short_circuits_after_atomic_rpc(self) -> None:
        with (
            patch.object(edit_bridge_service, "replace_ui_position_edit_atomic", return_value=True) as rpc,
            patch.object(edit_bridge_service, "list_adjustments") as list_adjustments,
            patch.object(edit_bridge_service, "remove_adjustments_by_code_date") as remove_rows,
            patch.object(edit_bridge_service, "add_adjustment") as add_row,
        ):
            edit_bridge_service.apply_position_edit(
                effective_date="2026-07-24",
                code="000001",
                shares_end=10,
                avg_cost_nav_end=1.1,
                realized_pnl_end=2,
            )

        rpc.assert_called_once()
        list_adjustments.assert_not_called()
        remove_rows.assert_not_called()
        add_row.assert_not_called()

    def test_invalid_numeric_target_is_rejected_before_rpc(self) -> None:
        with patch.object(edit_bridge_service, "replace_ui_position_edit_atomic") as rpc:
            with self.assertRaisesRegex(ValueError, "finite"):
                edit_bridge_service.apply_position_edit(
                    effective_date="2026-07-24",
                    code="000001",
                    shares_end=math.nan,
                    avg_cost_nav_end=1,
                )
        rpc.assert_not_called()

    def test_security_migration_contains_rls_uniques_and_transaction_rpc(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "supabase"
            / "migrations"
            / "202607240001_security_hardening.sql"
        ).read_text(encoding="utf-8")
        normalized = migration.lower()

        self.assertIn("begin;", normalized)
        self.assertIn("commit;", normalized)
        self.assertIn("enable row level security", normalized)
        self.assertIn("private.current_app_user_id", normalized)
        self.assertIn("create unique index if not exists app_watchlist_user_code_uidx", normalized)
        self.assertIn("create unique index if not exists app_daily_ledger_user_date_code_uidx", normalized)
        self.assertIn("create or replace function public.app_apply_position_edit", normalized)
        self.assertIn("pg_advisory_xact_lock", normalized)
        self.assertIn("security invoker", normalized)


if __name__ == "__main__":
    unittest.main()
