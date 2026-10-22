"""Minimal environment shared by the desktop window and frozen bot workers."""
import os
from pathlib import Path
import sys


if getattr(sys, "frozen", False):
    bundled_bin = Path(sys._MEIPASS) / "bin"
    os.environ["PATH"] = str(bundled_bin) + os.pathsep + os.environ.get("PATH", "")
    # A windowed executable has no stdout/stderr. Desktop entry configures
    # worker log files; this runtime hook intentionally leaves their routing to it.
