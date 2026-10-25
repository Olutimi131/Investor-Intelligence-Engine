"""
Streamlit Application Entrypoint
================================
Runs the Investor Intelligence Engine dashboard.

Supports both:
  - streamlit run app.py
  - python app.py [--host 127.0.0.1] [--port 8501]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ENTRYPOINT = PROJECT_ROOT / "streamlit_app.py"


def _is_running_under_streamlit() -> bool:
    """Detect if this script is executed directly by Streamlit's runtime runner."""
    try:
        from streamlit.runtime import exists

        return exists()
    except Exception:
        return False


if _is_running_under_streamlit():
    # Invoked as: `streamlit run app.py`
    # Directly execute streamlit_app.py in this Streamlit session.
    with open(DEFAULT_ENTRYPOINT, encoding="utf-8") as _f:
        _code = compile(_f.read(), str(DEFAULT_ENTRYPOINT), "exec")
        exec(_code, globals())
else:

    def main() -> int:
        parser = argparse.ArgumentParser(
            description="Run the Investor Intelligence Engine dashboard in Streamlit."
        )
        parser.add_argument("--host", default="127.0.0.1", help="Host interface for the dashboard")
        parser.add_argument("--port", type=int, default=8501, help="Port for the dashboard")
        parser.add_argument(
            "--file",
            type=Path,
            default=DEFAULT_ENTRYPOINT,
            help="Path to the Streamlit app script",
        )
        args = parser.parse_args()

        entrypoint = args.file.resolve() if args.file.is_absolute() else (PROJECT_ROOT / args.file).resolve()
        if not entrypoint.exists():
            raise FileNotFoundError(f"Streamlit entrypoint not found: {entrypoint}")

        cmd = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(entrypoint),
            "--server.address",
            args.host,
            "--server.port",
            str(args.port),
        ]
        return subprocess.call(cmd)

    if __name__ == "__main__":
        raise SystemExit(main())
