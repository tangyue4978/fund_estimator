from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.adjustment_service import migrate_ui_edit_source


def main() -> None:
    changed = migrate_ui_edit_source()
    print(f"ui_edit source backfilled: {changed}")


if __name__ == "__main__":
    main()
