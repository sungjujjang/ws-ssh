"""Shared wire protocol for the relay <-> agent link.

The relay talks to internal agents over a WebSocket carrying **binary** frames.
Every frame is a single protocol message with a fixed 5 byte header::

    +--------+------------------+-------------------+
    | type   | session id (u32) | payload           |
    | 1 byte | 4 bytes, BE      | variable          |
    +--------+------------------+-------------------+

The protocol is a raw byte stream, *not* a command/response RPC.  DATA frames
contain the exact bytes that travelled between the SSH client and the remote
shell's PTY, so ANSI escape sequences, control characters and partial UTF-8
sequences pass through untouched.

Session multiplexing
--------------------
A single agent keeps one WebSocket open to the relay and can serve several
concurrent operators.  Each operator shell is a *session* identified by an
unsigned 32 bit id.  Messages that belong to a session carry that id; control
messages (AUTH/PING/...) use session id ``0``.
"""

# NOTE: vendored copy. This file is duplicated as ``agent/protocol.py`` and
# ``relay/protocol.py`` so each folder can be shipped on its own. Keep identical.

from __future__ import annotations

import json
import struct
from typing import Any

