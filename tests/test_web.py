"""Tests for the install web server (relay/web.py)."""

import os
import tempfile
import urllib.error
import urllib.request

from relay.config import RelayConfig
from relay.db import RegistryDB
from relay.web import start_web_server


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


