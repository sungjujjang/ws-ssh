import struct

import pytest

from relay import protocol


def test_roundtrip_data():
    frame = protocol.encode(protocol.DATA, 42, b"\x1b[31mred\x1b[0m")
    msg_type, session_id, payload = protocol.decode(frame)
    assert msg_type == protocol.DATA
    assert session_id == 42
    assert payload == b"\x1b[31mred\x1b[0m"


def test_roundtrip_str_payload_is_utf8():
    frame = protocol.encode(protocol.CLOSE, 1, "안녕")
    _, _, payload = protocol.decode(frame)
    assert payload.decode("utf-8") == "안녕"


def test_binary_safe():
    # every byte value including NUL must survive untouched
    blob = bytes(range(256))
    frame = protocol.encode(protocol.DATA, 0xFFFFFFFF, blob)
    _, session_id, payload = protocol.decode(frame)
    assert session_id == 0xFFFFFFFF
    assert payload == blob


