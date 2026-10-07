"""End to end test: SSH client -> relay PTY -> WebSocket -> agent PTY -> shell.

This is the test that proves the transport is a raw byte stream and not an
``exec_command`` RPC: it drives an interactive bash, checks ANSI passthrough,
terminal resize propagation and control characters (Ctrl+C).
"""

import asyncio
import os
import shutil
import tempfile

import pytest

pytest.importorskip("asyncssh")
import asyncssh  # noqa: E402

from agent.agent import Agent, AgentConfig  # noqa: E402
from agent.pty_backend import default_shell  # noqa: E402
from relay.config import RelayConfig  # noqa: E402
from relay.db import RegistryDB  # noqa: E402
from relay.registry import Registry  # noqa: E402
from relay.ssh_server import RelaySession, start_ssh_server  # noqa: E402
from relay.ws_server import start_ws_server  # noqa: E402

BASH = shutil.which("bash") or shutil.which("sh")
STTY = shutil.which("stty")


class Expect:
    """Sequential byte matcher that keeps unconsumed data for the next match."""

    def __init__(self, stream):
        self.stream = stream
        self.buf = b""

    async def until(self, marker: bytes, timeout: float = 15.0) -> bytes:
        async def _run() -> None:
            while marker not in self.buf:
                chunk = await self.stream.read(65536)
                if not chunk:
                    raise AssertionError(
                        f"stream closed while waiting for {marker!r}; got {self.buf!r}"
                    )
                self.buf += chunk
            end = self.buf.index(marker) + len(marker)
            self.buf = self.buf[end:]

        await asyncio.wait_for(_run(), timeout)
        return self.buf


