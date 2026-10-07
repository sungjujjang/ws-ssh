"""In-memory registry of connected agents and their live shell sessions.

The registry is shared between the SSH server and the WebSocket server which
both run inside the same asyncio event loop.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any, Protocol

from relay import protocol

log = logging.getLogger("relay.registry")


class AgentSink(Protocol):
    """Minimal send interface the bridge needs from a transport."""

    async def send_raw(self, data: bytes) -> None: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


_session_ids = itertools.count(1)


def allocate_session_id() -> int:
    return next(_session_ids)


class SessionBridge:
    """Bridges one SSH session to one agent PTY session.

    ``client`` only has to expose an async ``write(bytes)`` method - it is the
    SSH channel in production and a fake in tests.
    """

    def __init__(self, session_id: int, agent: "AgentConnection", client: Any):
        self.session_id = session_id
        self.agent = agent
        self.client = client
        self.opened: asyncio.Future[dict] | None = None
        self.closed = False
        self._close_callbacks: list[Any] = []

    # -- called from the SSH side -----------------------------------------
    async def send_input(self, data: bytes) -> None:
        if self.closed:
            return
        await self.agent.send(protocol.DATA, self.session_id, data)

    async def send_resize(self, cols: int, rows: int) -> None:
        if self.closed:
            return
        await self.agent.send(protocol.RESIZE, self.session_id, protocol._RESIZE.pack(cols, rows))

