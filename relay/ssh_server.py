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

    def exec_requested(self, command: str) -> bool:
        # `ssh -t operator@relay attach <server>` (or just `<server>`) attaches
        # straight to that server's shell, skipping the C2 menu entirely.
        self._exec_command = command
        self._exec_target = self._parse_exec(command)
        self._session_mode_ready.set()
        return True

    def terminal_size_changed(self, width, height, pixwidth, pixheight) -> None:
        if width and height:
            self._cols, self._rows = width, height
        if self._bridge is not None:
            asyncio.ensure_future(self._bridge.send_resize(self._cols, self._rows))

    def data_received(self, data, datatype) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._inbound.put_nowait(data)

    def eof_received(self) -> bool:
        self._inbound.put_nowait(None)
        return False

    def connection_lost(self, exc) -> None:
        self.writing_allowed.set()
        self._inbound.put_nowait(None)
        if self._task is not None:
            self._task.cancel()

    def break_received(self, msec) -> bool:
        # Forward a real BREAK as CTRL+C is not possible; treat as ^C to the shell.
        if self._bridge is not None:
            asyncio.ensure_future(self._bridge.send_input(b"\x03"))
        return True

    # -- output helpers ----------------------------------------------------
    def _emit_text(self, text: str) -> None:
        if self._chan is None:
            return
        try:
            self._chan.write(text.encode("utf-8"))
        except (BrokenPipeError, ConnectionError, asyncssh.Error):
            pass

    def _emit_bytes(self, data: bytes) -> None:
        if self._chan is None:
            return
        try:
            self._chan.write(data)
        except (BrokenPipeError, ConnectionError, asyncssh.Error):
            pass

    # -- main loop ---------------------------------------------------------
    async def _run(self) -> None:
        try:
            if self.direct_server is not None:
                await self._attach_direct(self.direct_server)
            else:
                # Wait (briefly) for the client to tell us shell-vs-exec so we
                # don't flash the C2 menu before an exec attach.
                try:
                    await asyncio.wait_for(self._session_mode_ready.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                if self._exec_target is not None:
                    await self._attach_admin(self._exec_target)
                else:
                    self._emit_text(self.config.banner)
                    self._editor.set_prompt(self._prompt)
                    self._editor.render_prompt()
            while not self._exit_requested:
                data = await self._inbound.get()
                if data is None:
                    break
                if self._bridge is not None:
                    await self._bridge.send_input(data)
                    continue
                lines = self._editor.feed(data)
                for line in lines:
                    await self._handle_command(line)
                    if self._bridge is not None or self._exit_requested:
                        break
                if self._editor.eof:
                    break
                # The line editor already echoes each keystroke incrementally
                # (and redraws itself for Ctrl+C/arrows/history).  Only print a
                # fresh prompt once a command has completed, otherwise every
                # keypress would redraw the whole prompt+line.
                if lines and self._bridge is None and not self._exit_requested:
                    self._editor.render_prompt()
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            pass
        except (BrokenPipeError, ConnectionError, asyncssh.Error):
            pass
        except Exception:  # pragma: no cover - unexpected
            log.exception("session error for %s", self.username)
        finally:
            await self._cleanup()

    async def _cleanup(self) -> None:
        if self._bridge is not None:
            bridge, self._bridge = self._bridge, None
            try:
                await bridge.close(b"client disconnected")
            except Exception:  # pragma: no cover
                pass
        if self._chan is not None:
            try:
                self._chan.close()
            except Exception:  # pragma: no cover
                pass

    # -- command handling --------------------------------------------------
    @staticmethod
    def _split_args(line: str) -> list[str]:
        """Split a C2 command line into words.

        Plain input is split on whitespace (backslashes are kept verbatim so an
        unquoted password is never mangled).  When the line contains quotes we
        fall back to shell-like splitting so operators can pass passwords that
        contain spaces or other special characters, e.g.::

            add-server server-001 "p@ss !! word"
        """
        if "'" in line or '"' in line:
            try:
                return shlex.split(line)
            except ValueError:
                pass
        return line.split()

    async def _handle_command(self, raw_line: str) -> None:
        line = raw_line.strip()
        if not line:
            return
        self._editor.add_history(line)
        parts = self._split_args(line)
        command = parts[0].lower()
        args = parts[1:]

        if command in ("help", "?"):
            self._print_help()
        elif command in ("list", "servers", "ls"):
            self._print_servers()
        elif command in ("sessions", "who"):
            self._print_sessions()
        elif command in ("add-server", "addserver", "register"):
            await self._cmd_add_server(args)
        elif command in ("login", "connect", "attach"):
            await self._cmd_login(args)
        elif command in ("enable", "start"):
            await self._cmd_set_enabled(args, True)
        elif command in ("disable", "stop"):
            await self._cmd_set_enabled(args, False)
        elif command in ("remove-server", "del-server", "delete"):
            await self._cmd_remove_server(args)
        elif command in ("kick", "disconnect"):
            await self._cmd_kick(args)
        elif command in ("reset-token", "rotate-token"):
            await self._cmd_reset_token(args)
        elif command in ("users", "admins"):
            self._print_users()
        elif command in ("add-user", "add-admin"):
            await self._cmd_add_user(args)
        elif command in ("remove-user", "del-user", "remove-admin"):
            await self._cmd_remove_user(args)
        elif command in ("passwd", "password", "passwd-me"):
            await self._cmd_passwd(args)
        elif command in ("reset-password", "reset-passwd", "set-user-password"):
            await self._cmd_reset_password(args)
        elif command in ("set-password", "server-password"):
            await self._cmd_set_server_password(args)
        elif command in ("info", "sysinfo"):
            await self._cmd_info(args)
        elif command in ("net", "netstat", "network"):
            await self._cmd_net(args)
        elif command in ("procs", "ps", "top"):
            await self._cmd_procs(args)
        elif command in ("overview", "fleet", "all"):
            await self._cmd_overview(args)
        elif command in ("logs", "log", "tail"):
            self._cmd_logs(args)
        elif command in ("whoami", "me"):
            self._emit_text(
                f"{self.username} (\x1b[32moperator\x1b[0m)\r\n"
                if self.direct_server is None
                else f"{self.username} (server)\r\n"
            )
        elif command in ("ping",):
            self._emit_text("pong\r\n")
        elif command in ("exit", "quit", "logout", "bye"):
            self._emit_text("Bye.\r\n")
            self._exit_requested = True
        else:
            self._emit_text(f"\x1b[31munknown command:\x1b[0m {command} (try 'help')\r\n")

    def _print_help(self) -> None:
        self._emit_text(
            "\r\n\x1b[1mAvailable commands\x1b[0m\r\n"
            "  \x1b[36mlist\x1b[0m                        list known internal servers\r\n"
            "  \x1b[36msessions\x1b[0m                    show live session counts per server\r\n"
            "  \x1b[36madd-server <id> [pw]\x1b[0m        create a server and print its one-line installer\r\n"
            "  \x1b[36mlogin <server> [pw]\x1b[0m          attach to a server's shell (admin: no pw needed)\r\n"
            "  \x1b[36menable\x1b[0m <server>             allow the server / agent to connect\r\n"
            "  \x1b[36mdisable\x1b[0m <server>            block the server and drop its agent\r\n"
            "  \x1b[36mremove-server\x1b[0m <server>      delete a server and its credentials\r\n"
            "  \x1b[36mkick\x1b[0m <server>                drop the live agent connection\r\n"
            "  \x1b[36mreset-token\x1b[0m <server>          rotate the agent token and reprint the installer\r\n"
            "  \x1b[36moverview\x1b[0m (`fleet`)             all servers' resources at a glance\r\n"
            "  \x1b[36musers\x1b[0m                       list operator accounts\r\n"
            "  \x1b[36madd-user\x1b[0m <name> [pw]          create/update an operator account\r\n"
            "  \x1b[36mremove-user\x1b[0m <name>            delete an operator account\r\n"
            "  \x1b[36mpasswd\x1b[0m [pw]                   change your own operator password\r\n"
            "  \x1b[36mreset-password\x1b[0m <name> [pw]    set another operator's password\r\n"
            "  \x1b[36mset-password\x1b[0m <server> [pw]    change a server's login password\r\n"
            "  \x1b[36minfo\x1b[0m <server>                host, CPU, memory and disk summary\r\n"
            "  \x1b[36mnet\x1b[0m <server>                 interfaces, listening ports, connections\r\n"
            "  \x1b[36mprocs\x1b[0m <server> [n]             top n processes by CPU (default 15)\r\n"
            "  \x1b[36mlogs\x1b[0m [n]                    show the last n relay log lines\r\n"
            "  \x1b[36mwhoami\x1b[0m                      show which account you are using\r\n"
            "  \x1b[36mping\x1b[0m                        check the C2 CLI is responsive\r\n"
            "  \x1b[36mhelp\x1b[0m                        show this help\r\n"
            "  \x1b[36mexit\x1b[0m                        disconnect\r\n"
            "\r\n"
            "  \x1b[2mPasswords with spaces/special chars (e.g. '!!') should be quoted:\x1b[0m\r\n"
            "  \x1b[2m  add-server s1 \"p@ss !! word\"   (or omit it and type at the hidden prompt)\x1b[0m\r\n"
            "\r\n"
        )

    def _print_servers(self) -> None:
        servers = self.db.list_servers()
        if not servers:
            self._emit_text("No servers registered.\r\n")
            return
        counts = self.registry.session_counts()
        self._emit_text(
            "\r\n\x1b[1m  SERVER           STATUS     SESS  DESCRIPTION\x1b[0m\r\n"
        )
        for server in servers:
            online = self.registry.is_online(server.name)
            if not server.enabled:
                status = "\x1b[33mdisabled\x1b[0m"
            elif online:
                status = "\x1b[32monline  \x1b[0m"
            else:
                status = "\x1b[31moffline \x1b[0m"
            sessions = counts.get(server.name, 0) if online else 0
            self._emit_text(
                f"  {server.name:<16} {status} {sessions:>4}  {server.description}\r\n"
            )
        self._emit_text("\r\n")

    def _print_sessions(self) -> None:
        counts = {n: c for n, c in self.registry.session_counts().items() if c}
        if not counts:
            self._emit_text("No active sessions.\r\n")
            return
        self._emit_text("\r\n\x1b[1m  SERVER           ACTIVE SESSIONS\x1b[0m\r\n")
        for name in sorted(counts):
            self._emit_text(f"  {name:<16} {counts[name]}\r\n")
        self._emit_text("\r\n")

    def _print_users(self) -> None:
        users = self.db.list_relay_users()
        if not users:
            self._emit_text("No operator accounts.\r\n")
            return
        self._emit_text("\r\n\x1b[1m  OPERATOR\x1b[0m\r\n")
        for user in users:
            marker = " \x1b[32m(you)\x1b[0m" if user == self.username else ""
            self._emit_text(f"  {user}{marker}\r\n")
        self._emit_text("\r\n")

    def _default_host(self) -> str:
        if self.config.advertise_host:
            return self.config.advertise_host
        conn = self._conn
        if conn is not None:
            try:
                sockname = conn.get_extra_info("sockname")
            except Exception:  # pragma: no cover - defensive
                sockname = None
            if sockname:
                return sockname[0]
        return ""

    async def _cmd_add_server(self, args: list[str]) -> None:
        if not args:
            self._emit_text("usage: add-server <id> [password]\r\n")
            return
        name = args[0]
        if not _NAME_RE.match(name):
            self._emit_text(
                "\x1b[31minvalid server id\x1b[0m "
                "(lowercase letters, digits, '.', '_', '-'; start alphanumeric)\r\n"
            )
            return
        if self.db.get_server(name) is not None:
            self._emit_text(
                f"server {name} already exists; remove it with the manage CLI then retry\r\n"
            )
            return

        password = args[1] if len(args) > 1 else None
        if password is None:
            password = await self._read_password(f"Login password for new server {name}: ")
            if password is None:
                self._emit_text("cancelled\r\n")
                return
        if not password:
            self._emit_text("password must not be empty\r\n")
            return

        token = self.db.add_server(name, password, description="created from C2")
        host = self._default_host()
        self._emit_text(f"\r\n\x1b[32mServer '{name}' created.\x1b[0m\r\n\r\n")

        installer = install_command(self.config, name, token, host) if host else None
        if installer:
            self._emit_text(
                "Run this one-liner on the internal server (as root):\r\n\r\n"
                f"  \x1b[36m{installer}\x1b[0m\r\n\r\n"
                "It installs the agent, starts it now, and enables it on boot.\r\n"
                f"The server appears as \x1b[32monline\x1b[0m in 'list' once connected.\r\n"
            )
        else:
            self._emit_text(
                "Agent token (shown once - keep it secret):\r\n"
                f"  {token}\r\n\r\n"
                "On the internal server, run:\r\n"
                f"  {agent_command(self.config, name, token, host or None, None)}\r\n"
            )

    async def _cmd_login(self, args: list[str]) -> None:
        if not args:
            self._emit_text("usage: login <server> [password]\r\n")
            return
        name = args[0]
        password = args[1] if len(args) > 1 else None

        server = self.db.get_server(name)
        if server is None:
            self._emit_text(f"\x1b[31munknown server:\x1b[0m {name}\r\n")
            return
        if not server.enabled:
            self._emit_text(f"server {name} is disabled\r\n")
            return

        # Operators are already authenticated against the relay, so they may
        # attach without the per-server login password.  An explicit password is
        # still accepted (and verified) for scripted/back-compat use.
        if password is not None and not self.db.verify_server_login(name, password):
            self._emit_text("\x1b[31mauthentication failed\x1b[0m\r\n")
            return

        if await self._open_bridge(name):
            self._emit_text(f"\r\n\x1b[32mConnected to {name}\x1b[0m\r\n\r\n")

    @staticmethod
    def _parse_exec(command: str | None) -> str | None:
        """Interpret an SSH exec command as an admin attach target."""
        if not command:
            return None
        parts = command.strip().split()
        if not parts:
            return None
        if parts[0] in ("attach", "login", "connect") and len(parts) > 1:
            return parts[1]
        if len(parts) == 1 and _NAME_RE.match(parts[0]):
            return parts[0]
        return None

    async def _attach_admin(self, name: str) -> None:
        """Exec path: attach as an authenticated operator (no server password)."""
        server = self.db.get_server(name)
        if server is None:
            self._emit_text(f"\x1b[31munknown server:\x1b[0m {name}\r\n")
            self._exit_requested = True
            return
        if not server.enabled:
            self._emit_text(f"\x1b[31mserver {name} is disabled\x1b[0m\r\n")
            self._exit_requested = True
            return
        if not await self._open_bridge(name):
            self._exit_requested = True

    async def _attach_direct(self, name: str) -> None:
        """Non-admin path: go straight into the named server's shell."""
        server = self.db.get_server(name)
        if server is None:
            self._emit_text("\x1b[31mserver no longer exists\x1b[0m\r\n")
            self._exit_requested = True
            return
        if not server.enabled:
            self._emit_text(f"\x1b[31mserver {name} is disabled\x1b[0m\r\n")
            self._exit_requested = True
            return
        if not await self._open_bridge(name):
            self._exit_requested = True

    async def _open_bridge(self, name: str) -> bool:
        """Open a PTY session on ``name`` and mark the session as bridged."""
        agent = self.registry.get(name)
        if agent is None or agent.closed:
            self._emit_text(f"server {name} is offline (agent not connected)\r\n")
            return False

        loop = asyncio.get_event_loop()
        session_id = allocate_session_id()
        bridge = SessionBridge(session_id, agent, client=self._writer)
        bridge.opened = loop.create_future()
        agent.add_session(bridge)

        env = {
            "TERM": self._term_type,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "COLORTERM": "truecolor",
        }
        try:
            await agent.send(
                protocol.OPEN,
                session_id,
                json.dumps({"shell": None, "cols": self._cols, "rows": self._rows, "env": env}),
            )
        except ConnectionError:
            agent.remove_session(session_id)
            self._emit_text("agent connection lost\r\n")
            return False

        try:
            result = await asyncio.wait_for(bridge.opened, timeout=self.config.open_timeout)
        except asyncio.TimeoutError:
            await bridge.close(b"")
            self._emit_text("timed out opening remote shell\r\n")
            return False

        if not result or not result.get("ok"):
            error = (result or {}).get("error") or "unknown error"
            await bridge.close(b"")
            self._emit_text(f"\x1b[31mfailed to open shell:\x1b[0m {error}\r\n")
            return False

        bridge.on_closed(self._on_bridge_closed)
        self._bridge = bridge
        return True

    def _on_bridge_closed(self) -> None:
        # The remote shell exited (or the agent dropped).
        self._bridge = None
        if self._chan is None:
            return
        self._emit_text("\r\n\x1b[33m[disconnected from remote shell]\x1b[0m\r\n")
        if self.direct_server is not None:
            # Direct sessions are single-use; end the connection.
            self._exit_requested = True
            self._inbound.put_nowait(None)
            return
        self._editor.reset()
        self._editor.render_prompt()

    async def _cmd_set_enabled(self, args: list[str], enabled: bool) -> None:
        if not args:
            self._emit_text("usage: enable|disable <server>\r\n")
            return
        name = args[0]
        if not self.db.set_server_enabled(name, enabled):
            self._emit_text(f"\x1b[31munknown server:\x1b[0m {name}\r\n")
            return
        verb = "enabled" if enabled else "disabled"
        self._emit_text(f"server {name} {verb}\r\n")
        if not enabled:
            if await self.registry.disconnect(name, b"disabled by operator"):
                self._emit_text(f"live agent for {name} disconnected\r\n")

    async def _cmd_remove_server(self, args: list[str]) -> None:
        if not args:
            self._emit_text("usage: remove-server <server>\r\n")
            return
        name = args[0]
        await self.registry.disconnect(name, b"server removed by operator")
        if not self.db.remove_server(name):
            self._emit_text(f"\x1b[31munknown server:\x1b[0m {name}\r\n")
            return
        self._emit_text(f"server {name} removed\r\n")

