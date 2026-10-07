"""Registry management CLI.

Examples::

    python -m relay.manage init
    python -m relay.manage add-user alice            # prompts for a password
    python -m relay.manage add-user alice --password s3cret
    python -m relay.manage add-server server-001 --password op-pass --advertise-host relay.example.com
    python -m relay.manage list
    python -m relay.manage disable server-001
    python -m relay.manage remove-server server-001
    python -m relay.manage users
    python -m relay.manage remove-user alice
    python -m relay.manage reset-token server-001
    python -m relay.manage set-password server-001       # change a server login password
    python -m relay.manage set-user-password alice       # change an operator password
"""

from __future__ import annotations

import argparse
import getpass
import sys

from relay.config import RelayConfig
from relay.db import RegistryDB


def _format_host(host: str) -> str:
    host = (host or "").strip()
    if not host:
        return ""
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def install_command(config: RelayConfig, name: str, token: str, advertise_host: str | None) -> str | None:
    """Return the one-line ``curl ... | sudo bash`` installer, or None if web is off."""
    if not config.web_enabled:
        return None
    host = _format_host(advertise_host or config.advertise_host)
    if not host:
        return None
    return (
        f"curl -fsSL 'http://{host}:{config.web_port}/install/{name}"
        f"?token={token}' | sudo bash"
    )


def agent_command(config: RelayConfig, name: str, token: str, advertise_host: str | None, shell: str | None) -> str:
    host = _format_host(advertise_host or config.advertise_host) or "<relay-host>"
    command = (
        f"python -m agent --relay ws://{host}:{config.ws_port}{config.ws_path} "
        f"--id {name} --token {token}"
    )
    if shell:
        command += f" --shell {shell}"
    return command


def _prompt_password(prompt: str) -> str:
    first = getpass.getpass(prompt)
    second = getpass.getpass("Repeat: ")
    if first != second:
        raise SystemExit("passwords do not match")
    if not first:
        raise SystemExit("password must not be empty")
    return first


def _open_db(args: argparse.Namespace) -> RegistryDB:
    config = RelayConfig.load(args.config)
    db = RegistryDB(config.db_path)
    db.init_schema()
    return db


def cmd_init(args: argparse.Namespace) -> int:
    db = _open_db(args)
    print(f"initialized database at {db.path}")
    return 0


def cmd_add_user(args: argparse.Namespace) -> int:
    db = _open_db(args)
    password = args.password or _prompt_password(f"Password for SSH user {args.username}: ")
    db.add_relay_user(args.username, password)
    print(f"relay user '{args.username}' saved")
    return 0


def cmd_add_server(args: argparse.Namespace) -> int:
    config = RelayConfig.load(args.config)
    if args.advertise_host:
        config.advertise_host = args.advertise_host
    if args.web_port:
        config.web_port = args.web_port
    if args.ws_port:
        config.ws_port = args.ws_port
    db = RegistryDB(config.db_path)
    db.init_schema()

    password = args.password or _prompt_password(f"Login password for server {args.name}: ")
    token = db.add_server(
        args.name,
        password,
        description=args.description or "",
        token=args.token,
    )
    print(f"server '{args.name}' saved")
    print()
    print("Agent token (shown once - keep it secret):")
    print(f"  {token}")
    print()

    installer = install_command(config, args.name, token, args.advertise_host)
    if installer:
        print("On the internal server, run this one-liner (as root):")
        print()
        print(f"  {installer}")
        print()
        print("It installs the agent, starts it now, and enables it on boot.")
    else:
        if config.web_enabled and not (args.advertise_host or config.advertise_host):
            print("(pass --advertise-host to also print a one-line installer URL)")
        print("Run the agent on the internal server with:")
        print(f"  {agent_command(config, args.name, token, args.advertise_host, args.shell)}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    db = _open_db(args)
    servers = db.list_servers()
    if not servers:
        print("no servers registered")
        return 0
    width = max(len(s.name) for s in servers) + 2
    print(f"{'SERVER':<{width}} {'ENABLED':<8} DESCRIPTION")
    for server in servers:
        print(f"{server.name:<{width}} {str(server.enabled):<8} {server.description}")
    return 0


def cmd_set_enabled(args: argparse.Namespace, enabled: bool) -> int:
    db = _open_db(args)
    if not db.set_server_enabled(args.name, enabled):
        print(f"server '{args.name}' not found", file=sys.stderr)
        return 1
    print(f"server '{args.name}' {'enabled' if enabled else 'disabled'}")
    return 0


def cmd_remove_server(args: argparse.Namespace) -> int:
    db = _open_db(args)
    if not db.remove_server(args.name):
        print(f"server '{args.name}' not found", file=sys.stderr)
        return 1
    print(f"server '{args.name}' removed")
    return 0


def cmd_list_users(args: argparse.Namespace) -> int:
    db = _open_db(args)
    users = db.list_relay_users()
    if not users:
        print("no relay users registered")
        return 0
    for user in users:
        print(user)
    return 0


def cmd_remove_user(args: argparse.Namespace) -> int:
    db = _open_db(args)
    if not db.remove_relay_user(args.username):
        print(f"relay user '{args.username}' not found", file=sys.stderr)
        return 1
    print(f"relay user '{args.username}' removed")
    return 0


def cmd_set_server_password(args: argparse.Namespace) -> int:
    db = _open_db(args)
    if db.get_server(args.name) is None:
        print(f"server '{args.name}' not found", file=sys.stderr)
        return 1
    password = args.password or _prompt_password(f"New login password for server {args.name}: ")
    db.set_server_login_password(args.name, password)
    print(f"login password for server '{args.name}' updated")
    return 0


def cmd_set_user_password(args: argparse.Namespace) -> int:
    db = _open_db(args)
    if not db.relay_user_exists(args.username):
        print(f"relay user '{args.username}' not found", file=sys.stderr)
        return 1
    password = args.password or _prompt_password(f"New password for operator {args.username}: ")
    db.add_relay_user(args.username, password)
    print(f"password for operator '{args.username}' updated")
    return 0


def cmd_reset_token(args: argparse.Namespace) -> int:
    config = RelayConfig.load(args.config)
    if args.advertise_host:
        config.advertise_host = args.advertise_host
    if args.web_port:
        config.web_port = args.web_port
    if args.ws_port:
        config.ws_port = args.ws_port
    db = RegistryDB(config.db_path)
    db.init_schema()
    token = db.rotate_server_token(args.name, token=args.token)
    if token is None:
        print(f"server '{args.name}' not found", file=sys.stderr)
        return 1
    print(f"new agent token for '{args.name}' (shown once):")
    print(f"  {token}")
    installer = install_command(config, args.name, token, args.advertise_host)
    if installer:
        print()
        print("Reinstall the agent on the server with:")
        print(f"  {installer}")
    return 0


