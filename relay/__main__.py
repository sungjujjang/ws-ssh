"""Relay entry point: ``python -m relay --config config.yaml``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from relay.config import RelayConfig
from relay.db import RegistryDB
from relay.logs import install as install_log_buffer
from relay.registry import Registry
from relay.ssh_server import start_ssh_server
from relay.web import start_web_server
from relay.ws_server import start_ws_server

log = logging.getLogger("relay")


