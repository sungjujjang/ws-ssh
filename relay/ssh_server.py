"""SSH entry point and the C2 interactive shell.

Two ways to use the relay over SSH:

* **Operator / admin** - log in with a ``relay_users`` account.  The session
  runs the C2 command menu (a small line editor) with which servers can be
  created, connected to, disabled, removed, etc.
* **Direct user** - log in with the *server id* as the SSH username and the
  *server's login password* as the SSH password.  The session skips the menu
  and drops straight into that server's shell.

Flow::

    SSH client --(pty)--> RelaySession
                             |
                             |  'login <server> <password>'  (C2 CLI)
                             v
                        SessionBridge  <--raw bytes-->  agent WebSocket

Before login the session runs the C2 command menu (a small line editor).
After a successful ``login`` the session switches into *bridge mode*: every
byte from the SSH client is forwarded verbatim to the agent PTY and every byte
from the PTY is written straight back to the SSH channel.  No command parsing
happens in bridge mode, which is what makes ``vim``/``top``/``htop`` work.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex

import asyncssh

from relay import logs, protocol
from relay.config import RelayConfig
from relay.db import RegistryDB
from relay.lineeditor import LineEditor
from relay.manage import agent_command, install_command
from relay.registry import Registry, SessionBridge, allocate_session_id

log = logging.getLogger("relay.ssh")

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")


def _human_bytes(value: object) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(number) < 1024 or unit == "PiB":
            return f"{number:.0f} {unit}" if unit == "B" else f"{number:.1f} {unit}"
        number /= 1024
    return f"{number:.1f} PiB"


def _human_duration(seconds: object) -> str:
    if seconds is None:
        return "-"
    try:
        total = int(float(seconds))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def load_or_create_host_key(path: str) -> asyncssh.SSHKey:
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        key = asyncssh.generate_private_key("ssh-ed25519")
        with open(path, "wb") as handle:
            handle.write(key.export_private_key())
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - best effort
            pass
        log.info("generated new relay host key at %s", path)
        return key
    with open(path, "rb") as handle:
        return asyncssh.import_private_key(handle.read())


class ChannelWriter:
    """Adapts an asyncssh channel to the async ``write`` the bridge expects.

    asyncssh exposes backpressure through the session's ``pause_writing`` /
    ``resume_writing`` callbacks rather than a ``drain()`` coroutine.  We wait
    on the session's gate before delivering the next chunk so a slow SSH client
    naturally throttles the agent (and therefore the remote shell).
    """

    def __init__(self, chan: asyncssh.SSHServerChannel, session: "RelaySession"):
        self._chan = chan
        self._session = session
        self._closed = False

    async def write(self, data: bytes) -> None:
        if self._closed:
            return
        try:
            self._chan.write(data)
        except (BrokenPipeError, ConnectionError, asyncssh.Error):
            self._closed = True
            return
        gate = self._session.writing_allowed
        if not gate.is_set():
            await gate.wait()

    def close(self) -> None:
        self._closed = True


class RelaySession(asyncssh.SSHServerSession):
    def __init__(
        self,
        username: str,
        db: RegistryDB,
        registry: Registry,
        config: RelayConfig,
        conn: "asyncssh.SSHServerConnection | None" = None,
        direct_server: str | None = None,
    ):
        self.username = username
        self.db = db
        self.registry = registry
        self.config = config
        self._conn = conn
        # When set, the session bypasses the C2 menu and attaches straight to
        # this server (non-admin access via server id + login password).
        self.direct_server = direct_server

        self._chan: asyncssh.SSHServerChannel | None = None
        self._writer: ChannelWriter | None = None
        self._inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._exec_command: str | None = None
        self._exec_target: str | None = None
        # Set once the client tells us whether it wants a shell or ran an exec
        # command, so we know whether to show the C2 menu or attach directly.
        self._session_mode_ready = asyncio.Event()

        self._term_type = config.default_term
        self._cols = 80
        self._rows = 24
        self._bridge: SessionBridge | None = None
        self._exit_requested = False
        # Set == the SSH channel is accepting writes; cleared while asyncssh
        # has paused us because the client's receive window is full.
        self.writing_allowed = asyncio.Event()
        self.writing_allowed.set()

        self._editor = LineEditor(prompt="C2> ", emit=self._emit_text, completer=self._complete)
        self._prompt = "C2> "

    # -- asyncssh SSHServerSession hooks ----------------------------------
    def connection_made(self, chan: asyncssh.SSHServerChannel) -> None:
        self._chan = chan
        self._writer = ChannelWriter(chan, self)
        self._task = asyncio.ensure_future(self._run())

    def pause_writing(self) -> None:
        self.writing_allowed.clear()

    def resume_writing(self) -> None:
        self.writing_allowed.set()

    def pty_requested(self, term_type, term_size, term_modes) -> bool:
        if term_type:
            self._term_type = term_type
        if term_size:
            self._cols, self._rows = term_size[0], term_size[1]
        return True

    def shell_requested(self) -> bool:
        self._session_mode_ready.set()
        return True

