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

PROTOCOL_VERSION = 1

# --- message types ---------------------------------------------------------
# control
AUTH = 0x01          # agent -> relay : {"id","token","version","hostname","os"}
AUTH_OK = 0x02       # relay -> agent
AUTH_FAIL = 0x03     # relay -> agent : reason
PING = 0x30
PONG = 0x31
EXIT = 0x40          # either side : agent shutting the connection down

# system / network introspection (control channel, no PTY session allocated)
SYSINFO = 0x50       # relay -> agent : {"cmd":"info"|"net"|"procs","args":{...}}
SYSINFO_RES = 0x51   # agent -> relay : {"cmd":str,"ok":bool,"error":str|None,"data":any}

# session lifecycle
OPEN = 0x10          # relay -> agent : {"shell","cols","rows","env"}
OPENED = 0x11        # agent -> relay : {"ok":bool,"error":str|None}
CLOSE = 0x22         # either side : session is finished (payload = final bytes)
DATA = 0x20          # both : raw terminal bytes
RESIZE = 0x21        # both : ">HH" cols, rows

_HEADER = struct.Struct(">BI")
HEADER_SIZE = _HEADER.size
_RESIZE = struct.Struct(">HH")

# Guard against a peer sending an absurdly large frame.  The relay and agent
# both set their WebSocket max_size to this value.
MAX_FRAME_SIZE = 4 * 1024 * 1024


