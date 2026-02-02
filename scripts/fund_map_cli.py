# scripts/fund_map_cli.py
from __future__ import annotations

import argparse

from datasources.fund_profile_provider import FundProfileDTO
from datasources.fund_profile_jsonmap import upsert_one, ensure_map_file


def _bool01(x: str) -> bool:
    s = str(x).strip().lower()
    return s in ("1", "true", "yes", "y", "t")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fund profile map CLI (data/fund_profile_map.json)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="add/update a fund profile mapping")
    p_add.add_argument("code", type=str, help="fund code, e.g. 510300")
    p_add.add_argument("name", type=str, help="fund name, e.g. 沪深300ETF")
    p_add.add_argument("--type", dest="fund_type", type=str, default="", help="fund type text, e.g. ETF/指数/QDII")
    p_add.add_argument("--etf", dest="is_etf", type=str, default="0", help="1/0")
    p_add.add_argument("--qdii", dest="is_qdii", type=str, default="0", help="1/0")
    p_add.add_argument("--track", dest="track_index", type=str, default="", help="track index code, optional")

    p_show = sub.add_parser("show", help="show map file location")

    args = parser.parse_args()

    if args.cmd == "show":
        print(ensure_map_file())
        return

    if args.cmd == "add":
        code = args.code.strip()
        name = args.name.strip()

        dto = FundProfileDTO(
            code=code,
            name=name,
            fund_type=(args.fund_type or "").strip(),
            is_etf=_bool01(args.is_etf),
            is_qdii=_bool01(args.is_qdii),
            track_index=(args.track_index.strip() or None),
        )
        upsert_one(code, dto)
        print(f"OK: updated {code} -> {name}")
        print(f"Map file: {ensure_map_file()}")
        return


if __name__ == "__main__":
    main()
