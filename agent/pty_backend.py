"""PTY backends used by the internal agent.

An agent session is a *real* pseudo terminal running the user's shell.  On
POSIX systems this is :func:`pty.fork`; on Windows it is ConPTY (via the
optional ``pywinpty`` package).  Both backends expose the same tiny surface::

    pty.write(bytes)          # keystrokes into the shell
    await pty.read() -> bytes # shell output (b"" == EOF)
    pty.resize(cols, rows)    # TIOCSWINSZ / ConPTY resize
    pty.close()
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import struct
import sys
from typing import Mapping, Sequence


def default_shell() -> str:
    if sys.platform == "win32":
        for candidate in ("pwsh.exe", "powershell.exe", "cmd.exe"):
            found = shutil.which(candidate)
            if found:
                return found
        return "cmd.exe"
    # Prefer bash explicitly.  ``$SHELL`` is unreliable under systemd (it is
    # frequently unset or set to ``/bin/sh``), and the interactive shell must
    # not silently fall back to a POSIX ``sh``.
    for candidate in ("/bin/bash", "/usr/bin/bash", "/usr/local/bin/bash"):
        if os.path.exists(candidate):
            return candidate
    shell = os.environ.get("SHELL")
    if shell and os.path.exists(shell):
        return shell
    return "/bin/sh"


def default_cwd() -> str | None:
    """Home directory of the agent's user; the shell starts here by default."""
    try:
        home = os.path.expanduser("~")
    except Exception:  # pragma: no cover - defensive
        return None
    if home and home != "~" and os.path.isdir(home):
        return home
    return None


class UnixPty:
    """POSIX pseudo terminal backed by a master fd registered with the loop."""

    HIGH_WATER = 256
    LOW_WATER = 64
    READ_SIZE = 65536

