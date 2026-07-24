from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        import streamlit as st

        return str(st.secrets.get(name, "") or "").strip()
    except Exception:
        return ""


def _module_checks(names: Iterable[str]) -> list[Check]:
    out: list[Check] = []
    for name in names:
        try:
            module = importlib.import_module(name)
            version = str(getattr(module, "__version__", "") or "available")
            out.append(Check(f"module:{name}", True, version))
        except Exception as exc:
            out.append(Check(f"module:{name}", False, type(exc).__name__))
    return out


def run_checks(*, require_cloud: bool = False) -> list[Check]:
    checks: list[Check] = [
        Check(
            "python",
            sys.version_info >= (3, 11),
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        ),
        Check(
            "entrypoint",
            (PROJECT_ROOT / "app" / "Home.py").is_file(),
            "app/Home.py",
        ),
        Check(
            "compatibility-entrypoint",
            (PROJECT_ROOT / "main.py").is_file() and (PROJECT_ROOT / "main.py").stat().st_size > 0,
            "main.py",
        ),
    ]
    page_files = sorted((PROJECT_ROOT / "app" / "pages").glob("*.py"))
    checks.append(Check("pages", len(page_files) >= 5, f"{len(page_files)} discovered"))
    checks.extend(_module_checks(("streamlit", "pandas", "plotly", "requests")))

    try:
        data_root = PROJECT_ROOT / "data"
        data_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".preflight-", dir=data_root, delete=True):
            pass
        checks.append(Check("data-dir-writable", True, str(data_root)))
    except Exception as exc:
        checks.append(Check("data-dir-writable", False, type(exc).__name__))

    supabase_url = _secret("SUPABASE_URL")
    supabase_key = _secret("SUPABASE_KEY")
    cookie_secret = _secret("AUTH_COOKIE_SECRET")
    checks.extend(
        [
            Check(
                "supabase-config",
                bool(supabase_url and supabase_key),
                "configured" if supabase_url and supabase_key else "missing",
                required=require_cloud,
            ),
            Check(
                "auth-cookie-secret",
                len(cookie_secret) >= 32,
                "configured" if cookie_secret else "missing",
                required=require_cloud,
            ),
            Check(
                "secret-separation",
                bool(cookie_secret) and cookie_secret != supabase_key,
                "independent" if cookie_secret and cookie_secret != supabase_key else "not verified",
                required=require_cloud,
            ),
        ]
    )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Fund Estimator deployment preflight")
    parser.add_argument(
        "--require-cloud",
        action="store_true",
        help="Fail when production cloud/auth secrets are missing or unsafe.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    args = parser.parse_args()
    checks = run_checks(require_cloud=args.require_cloud)
    failed = [check for check in checks if check.required and not check.ok]

    if args.json:
        print(json.dumps([asdict(check) for check in checks], ensure_ascii=False, indent=2))
    else:
        for check in checks:
            status = "PASS" if check.ok else ("FAIL" if check.required else "WARN")
            print(f"[{status}] {check.name}: {check.detail}")
        print(f"\nPreflight: {len(checks) - len(failed)}/{len(checks)} required checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
