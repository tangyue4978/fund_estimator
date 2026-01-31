from __future__ import annotations

import sys
from pathlib import Path

from storage import paths
paths.ensure_dirs()


def setup_project_path() -> None:
    """
    让 Streamlit 能 import 项目根目录下的 services/datasources/... 模块
    """
    # app/bootstrap.py -> app -> project_root
    project_root = Path(__file__).resolve().parents[1]
    root_str = str(project_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
