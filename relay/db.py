"""SQLite backed registry of relay operators and internal servers.

Two kinds of credentials live here:

* ``relay_users``  - who may open an SSH session on the relay itself.
* ``servers``      - the internal machines.  Each row stores

  - ``token_hash``          the secret the *agent* uses to authenticate.
  - ``login_password_hash`` the secret an operator types after ``login <name>``.

All secrets are stored hashed (see :mod:`relay.security`); the plaintext agent
token is only ever shown once, when the server is created.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Iterable

from relay.security import hash_secret, new_token, verify_secret

_SCHEMA = """
CREATE TABLE IF NOT EXISTS relay_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS servers (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT UNIQUE NOT NULL,
    token_hash         TEXT NOT NULL,
    login_password_hash TEXT NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    enabled            INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Server:
    id: int
    name: str
    token_hash: str
    login_password_hash: str
    description: str
    enabled: bool


