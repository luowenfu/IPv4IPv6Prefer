"""Windows administrator elevation helpers."""

from __future__ import annotations

import ctypes
import sys
from typing import NoReturn


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """
    Relaunch the current process elevated via UAC.

    Returns True if ShellExecute was triggered successfully (caller should exit).
    Returns False if elevation could not be started.
    """
    if sys.platform != "win32":
        return False

    params = " ".join(f'"{arg}"' for arg in sys.argv[1:])
    # When frozen by PyInstaller, sys.executable is the exe itself.
    executable = sys.executable
    # In source mode, re-run the main script with the same interpreter.
    if not getattr(sys, "frozen", False):
        script = sys.argv[0]
        params = f'"{script}"' + (f" {params}" if params else "")

    ret = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        executable,
        params,
        None,
        1,  # SW_SHOWNORMAL
    )
    # Per MSDN, values > 32 indicate success.
    return int(ret) > 32


def ensure_admin_or_relaunch() -> bool:
    """
    Ensure the process is elevated.

    Returns True if already admin.
    If not admin and relaunch succeeds, exits the current process.
    If relaunch fails, returns False.
    """
    if is_admin():
        return True
    if relaunch_as_admin():
        sys.exit(0)
    return False
