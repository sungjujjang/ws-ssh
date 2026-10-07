import struct

import pytest

from relay import protocol


def test_roundtrip_data():
    frame = protocol.encode(protocol.DATA, 42, b"\x1b[31mred\x1b[0m")
    msg_type, session_id, payload = protocol.decode(frame)
    assert msg_type == protocol.DATA
    assert session_id == 42
    assert payload == b"\x1b[31mred\x1b[0m"


