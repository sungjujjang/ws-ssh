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


