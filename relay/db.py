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


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class RegistryDB:
    """Thin synchronous wrapper around a SQLite database file."""

    def __init__(self, path: str):
        self.path = path

    # -- plumbing ----------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # -- relay operators ---------------------------------------------------
    def add_relay_user(self, username: str, password: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO relay_users (username, password_hash, created_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash",
                (username, hash_secret(password), _now()),
            )

    def verify_relay_user(self, username: str, password: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT password_hash FROM relay_users WHERE username = ?", (username,)
            ).fetchone()
        return bool(row) and verify_secret(password, row["password_hash"])

    def relay_user_exists(self, username: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM relay_users WHERE username = ?", (username,)
            ).fetchone()
        return row is not None

