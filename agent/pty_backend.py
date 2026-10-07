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

    def __init__(
        self,
        argv: Sequence[str],
        cols: int,
        rows: int,
        env: Mapping[str, str],
        cwd: str | None = None,
    ):
        import fcntl
        import pty
        import termios

        self._fcntl = fcntl
        self._termios = termios
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._outbuf = b""
        self._reader_registered = False
        self._writer_registered = False
        self._eof = False
        self._closed = False

        pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover - child never returns
            try:
                if cwd:
                    os.chdir(cwd)
                signal.signal(signal.SIGINT, signal.SIG_DFL)
                signal.signal(signal.SIGTERM, signal.SIG_DFL)
                os.execvpe(argv[0], list(argv), dict(env))
            except BaseException:
                os._exit(127)

        self.pid = pid
        self.fd = fd
        self.resize(cols, rows)
        os.set_blocking(fd, False)
        self._loop.add_reader(fd, self._on_readable)
        self._reader_registered = True

    # -- reading -----------------------------------------------------------
    def _on_readable(self) -> None:
        if self._closed:
            return
        try:
            data = os.read(self.fd, self.READ_SIZE)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if data:
            self._queue.put_nowait(data)
            if self._queue.qsize() >= self.HIGH_WATER:
                self._pause_reader()
        else:
            self._pause_reader()
            self._eof = True
            self._queue.put_nowait(None)

    def _pause_reader(self) -> None:
        if self._reader_registered:
            self._loop.remove_reader(self.fd)
            self._reader_registered = False

    def _resume_reader(self) -> None:
        if not self._reader_registered and not self._eof and not self._closed:
            self._loop.add_reader(self.fd, self._on_readable)
            self._reader_registered = True

    async def read(self) -> bytes:
        item = await self._queue.get()
        if item is None:
            return b""
        if not self._reader_registered and self._queue.qsize() <= self.LOW_WATER:
            self._resume_reader()
        return item

    # -- writing -----------------------------------------------------------
    def write(self, data: bytes) -> None:
        if self._closed or not data:
            return
        self._outbuf += data
        self._flush()

    def _flush(self) -> None:
        while self._outbuf:
            try:
                written = os.write(self.fd, self._outbuf)
            except BlockingIOError:
                if not self._writer_registered:
                    self._loop.add_writer(self.fd, self._on_writable)
                    self._writer_registered = True
                return
            except OSError:
                self.close()
                return
            if written <= 0:
                return
            self._outbuf = self._outbuf[written:]
        if self._writer_registered:
            self._loop.remove_writer(self.fd)
            self._writer_registered = False

