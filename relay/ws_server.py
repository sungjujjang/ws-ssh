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


async def _handle_agent(db: RegistryDB, registry: Registry, config: RelayConfig, ws) -> None:
    peer = getattr(ws, "remote_address", None)
    agent: AgentConnection | None = None
    try:
        first = await asyncio.wait_for(ws.recv(), timeout=config.auth_timeout)
        if isinstance(first, str):
            first = first.encode("utf-8")
        msg_type, _session, payload = protocol.decode(first)
        if msg_type != protocol.AUTH:
            await ws.send(protocol.encode(protocol.AUTH_FAIL, 0, "expected AUTH"))
            await ws.close(code=4001, reason="protocol error")
            return

        info = protocol.decode_json(payload)
        name = info.get("id")
        token = info.get("token")
        if not isinstance(name, str) or not isinstance(token, str):
            await ws.send(protocol.encode(protocol.AUTH_FAIL, 0, "malformed AUTH"))
            await ws.close(code=4001, reason="protocol error")
            return

        if not db.verify_agent_token(name, token):
            log.warning("agent auth failed for %r from %s", name, peer)
            await ws.send(protocol.encode(protocol.AUTH_FAIL, 0, "authentication failed"))
            await ws.close(code=4001, reason="authentication failed")
            return

        agent = AgentConnection(name, WebSocketSink(ws), info)
        await registry.add(agent)
        await agent.send(
            protocol.AUTH_OK,
            0,
            json.dumps(
                {
                    "version": protocol.PROTOCOL_VERSION,
                    "server": name,
                }
            ),
        )
        log.info(
            "agent %s connected from %s (host=%s os=%s)",
            name,
            peer,
            info.get("hostname"),
            info.get("os"),
        )

        async for raw in ws:
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            try:
                await agent.handle_frame(raw)
            except protocol.ProtocolError as exc:
                log.warning("bad frame from agent %s: %s", name, exc)
    except asyncio.TimeoutError:
        log.warning("agent from %s timed out during authentication", peer)
        try:
            await ws.close(code=4008, reason="auth timeout")
        except Exception:
            pass
    except ConnectionClosed:
        pass
    except Exception:  # pragma: no cover - defensive
        log.exception("agent handler error for %s", peer)
    finally:
        if agent is not None:
            await agent.shutdown(b"agent disconnected")
            await registry.remove(agent)
            log.info("agent %s disconnected", agent.server_name)


