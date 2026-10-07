"""Password / token hashing helpers.

We deliberately stick to the standard library (PBKDF2-HMAC-SHA256) so the
relay has no native build dependencies.  Secrets are stored as::

    pbkdf2_sha256$<iterations>$<base64 salt>$<base64 derived key>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

_ALGORITHM = "pbkdf2_sha256"
_DEFAULT_ITERATIONS = 240_000


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


