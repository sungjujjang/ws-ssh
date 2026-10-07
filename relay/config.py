"""Relay configuration loading."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml

_ART = (
    "██╗   ██╗   ██████╗               ██████╗    ██████╗    ██╗  ██╗\r\n"
    "██║   ██║   ██╔════╝              ██╔════╝   ██╔════╝   ██║  ██║\r\n"
    "██║   ██║   ╚█████╗    ██████╗    ╚█████╗    ╚█████╗    ████████║\r\n"
    "██║▄▄ ██║    ╚═══██╗   ╚═════╝     ╚═══██╗    ╚═══██╗   ██╔══██║\r\n"
    "╚██████╔╝   ██████╔╝              ██████╔╝   ██████╔╝   ██║  ██║\r\n"
    "╚═╝   ╚═╝   ╚═════╝               ╚═════╝    ╚═════╝    ╚═╝  ╚═╝"
)

DEFAULT_BANNER = (
    "\r\n"
    "\x1b[1;36m" + _ART + "\x1b[0m\r\n"
    "\r\n"
    "\x1b[1mWS-SSH\x1b[0m \x1b[2m·\x1b[0m \x1b[2mSecure SSH Relay\x1b[0m\r\n"
    "\x1b[2mAuthorized operators only.\x1b[0m "
    "\x1b[2mType\x1b[0m \x1b[1mhelp\x1b[0m \x1b[2mfor commands.\x1b[0m\r\n"
    "\r\n"
)


@dataclass
class RelayConfig:
    ssh_host: str = "0.0.0.0"
    ssh_port: int = 2222
    ws_host: str = "0.0.0.0"
    ws_port: int = 8765
    ws_path: str = "/agent"
    host_key: str = "data/relay_host_key"
    authorized_keys: str | None = "data/authorized_keys"
    db_path: str = "data/relay.db"
    allow_anonymous: bool = False
    default_term: str = "xterm-256color"
