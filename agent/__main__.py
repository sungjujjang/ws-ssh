"""Agent entry point: ``python -m agent --relay ... --id ... --token ...``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal

import yaml

from agent.agent import Agent, AgentConfig

log = logging.getLogger("agent")


def _load_config_file(path: str | None) -> dict:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data.get("agent", data) if isinstance(data, dict) else {}


