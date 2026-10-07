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


async def amain(config: RelayConfig) -> None:
    db = RegistryDB(config.db_path)
    db.init_schema()

    registry = Registry()
    ssh_listener = await start_ssh_server(db, registry, config)
    ws_server = await start_ws_server(db, registry, config)

    web_server = None
    if config.web_enabled:
        web_server = start_web_server(db, config)

    log.info("SSH server listening on %s:%s", config.ssh_host, config.ssh_port)
    log.info(
        "Agent WebSocket listening on ws://%s:%s%s",
        config.ws_host,
        config.ws_port,
        config.ws_path,
    )
    if web_server is not None:
        log.info(
            "install server listening on http://%s:%s (advertise_host=%r)",
            config.web_host,
            config.web_port,
            config.advertise_host or "<auto>",
        )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - windows
            pass
    await stop.wait()

    log.info("shutting down")
    await registry.shutdown()
    ssh_listener.close()
    await ssh_listener.wait_closed()
    ws_server.close()
    await ws_server.wait_closed()
    if web_server is not None:
        web_server.close()


