"""Entry point used by the per-user Run entry and the Start menu shortcuts (pythonw.exe -E -s companion_main.py serve).

-E -s ignore PYTHON* variables and the user's site-packages; this folder holds the companion and the agent core.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from skyguard_companion.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
