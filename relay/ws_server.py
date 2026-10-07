"""WebSocket endpoint that internal agents connect to.

The agent authenticates with ``{"id": <server name>, "token": <secret>}`` and
then keeps the connection open.  Every subsequent frame is a binary protocol
frame (see :mod:`relay.protocol`).  The module is a vendored copy of
``agent/protocol.py``; the two must stay byte-for-byte identical.
"""

from __future__ import annotations

import asyncio
import json
import logging

import websockets
from websockets.exceptions import ConnectionClosed

from relay import protocol
from relay.config import RelayConfig
from relay.db import RegistryDB
from relay.registry import AgentConnection, Registry

log = logging.getLogger("relay.ws")

AUTH_TIMEOUT = 30.0


class WebSocketSink:
    """Thin adapter so the registry can send raw frames without knowing websockets."""

    def __init__(self, ws):
        self._ws = ws

    async def send_raw(self, data: bytes) -> None:
        await self._ws.send(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self._ws.close(code=code, reason=reason)


