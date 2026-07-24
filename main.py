"""Compatibility entry point for local and hosted Streamlit deployments."""

from pathlib import Path
import runpy


APP_ENTRYPOINT = Path(__file__).resolve().parent / "app" / "Home.py"
runpy.run_path(str(APP_ENTRYPOINT), run_name="__main__")
