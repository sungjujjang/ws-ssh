"""Internal agent: connects out to the relay and serves real PTY shells.

The agent dials the relay (so no inbound firewall rule is needed), authenticates
with its server id + token, and then keeps the WebSocket open.  When an operator
logs in via the C2 CLI the relay sends ``OPEN``; the agent forks a PTY running
the shell and streams bytes in both directions until the shell exits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import socket
from dataclasses import dataclass, field

import websockets
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from agent import protocol, sysinfo
from agent.pty_backend import create_pty, default_shell

log = logging.getLogger("agent")


class AuthError(Exception):
    pass


@dataclass
class AgentConfig:
    relay_url: str
    server_id: str
    token: str
    shell: str | None = None
    cwd: str | None = None
    verify_tls: bool = True
    ping_interval: float = 20.0
    ping_timeout: float = 20.0
    reconnect_min: float = 1.0
    reconnect_max: float = 30.0
    env: dict[str, str] = field(default_factory=dict)


class AgentSession:
    def __init__(
        self,
        agent: "Agent",
        session_id: int,
        shell: str,
        cols: int,
        rows: int,
        env: dict[str, str],
        cwd: str | None,
    ):
        self.agent = agent
        self.session_id = session_id
        self.pty = create_pty(shell, cols, rows, env, cwd)
        self._task: asyncio.Task | None = None
        self._closed = False

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._pump())

