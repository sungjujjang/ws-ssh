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

    async def close(self, reason: bytes = b"") -> None:
        if self.closed:
            return
        self.closed = True
        await self.agent.send(protocol.CLOSE, self.session_id, reason)
        self.agent.remove_session(self.session_id)
        await self._fire_callbacks()

    # -- called from the agent WebSocket side -----------------------------
    async def feed_client(self, data: bytes) -> None:
        if self.closed:
            return
        await self.client.write(data)

    async def on_agent_close(self, final: bytes) -> None:
        if self.closed:
            return
        self.closed = True
        if final:
            try:
                await self.client.write(final)
            except Exception:  # pragma: no cover - client may already be gone
                pass
        self.agent.remove_session(self.session_id)
        await self._fire_callbacks()

    def on_closed(self, callback) -> None:
        self._close_callbacks.append(callback)

    async def _fire_callbacks(self) -> None:
        callbacks, self._close_callbacks = self._close_callbacks, []
        for callback in callbacks:
            try:
                result = callback()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # pragma: no cover - defensive
                log.exception("session close callback failed")


class AgentConnection:
    """A single agent WebSocket plus the sessions multiplexed over it."""

    def __init__(self, server_name: str, sink: AgentSink, info: dict[str, Any] | None = None):
        self.server_name = server_name
        self.sink = sink
        self.info = info or {}
        self.sessions: dict[int, SessionBridge] = {}
        self._send_lock = asyncio.Lock()
        self._closed = False
        self._info_pending: dict[int, asyncio.Future] = {}
        self.connected_at = asyncio.get_event_loop().time()

    async def send(self, msg_type: int, session_id: int = 0, payload: bytes | str = b"") -> None:
        if self._closed:
            raise ConnectionError("agent connection is closed")
        async with self._send_lock:
            await self.sink.send_raw(protocol.encode(msg_type, session_id, payload))

