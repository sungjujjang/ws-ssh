"""Tests for the SQLite registry (relay/db.py)."""

import os
import tempfile

from relay.db import RegistryDB


def _db() -> RegistryDB:
    tmp = tempfile.mkdtemp(prefix="ws-db-")
    db = RegistryDB(os.path.join(tmp, "relay.db"))
    db.init_schema()
    return db


def test_operator_accounts():
    db = _db()
    db.add_relay_user("alice", "pw1")
    db.add_relay_user("bob", "pw2")
    assert db.list_relay_users() == ["alice", "bob"]
    assert db.count_relay_users() == 2
    assert db.verify_relay_user("alice", "pw1")
    assert not db.verify_relay_user("alice", "wrong")

    assert db.remove_relay_user("bob")
    assert not db.remove_relay_user("bob")
    assert db.list_relay_users() == ["alice"]


