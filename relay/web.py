"""Install web server (default port 1234).

Small, dependency-free HTTP server that makes provisioning an internal server a
single command.  It serves three things:

``GET /install/<server-id>?token=<agent-token>``
    A short bash bootstrap that downloads the agent package and runs its
    installer.  The token is validated against the registry first, so a link
    only works for the server it was minted for.

``GET /agent.tar.gz``
    The agent package (the ``agent/`` folder).  Not secret - the token in the
    install URL is what authorises registration.

``GET /health``
    Liveness probe.

The server runs in its own thread so it never interferes with the asyncio
relay loop.  Put it behind TLS (nginx/caddy) in production.
"""

from __future__ import annotations

import logging
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from relay.config import RelayConfig
from relay.db import RegistryDB

log = logging.getLogger("relay.web")

INSTALL_PATH = "/install/"
PACKAGE_PATH = "/agent.tar.gz"

_MARK_SVG = (
    '<svg width="72" height="72" viewBox="0 0 160 160" aria-hidden="true">'
    '<rect x="4" y="4" width="152" height="152" rx="38" fill="#0c1a2a" '
    'stroke="#3ddc84" stroke-width="6"/>'
    '<path d="M 48 52 L 84 84 L 48 116" fill="none" stroke="#3ddc84" '
    'stroke-width="14" stroke-linecap="round" stroke-linejoin="round"/>'
    '<rect x="98" y="104" width="36" height="14" rx="7" fill="#3ddc84"/>'
    '<rect x="98" y="54" width="20" height="14" rx="7" fill="#2b7fff" '
    'opacity="0.85"/></svg>'
)

_LANDING = b"""<!doctype html>
<html><head><meta charset="utf-8"><title>WS-SSH</title></head>
<body style="font-family:monospace;padding:2rem;background:#0a0f16;color:#e6edf3">
<div style="display:flex;align-items:center;gap:1rem">
""" + _MARK_SVG.encode() + b"""
<div><h1 style="margin:0">WS-SSH</h1>
<p style="margin:0;color:#7f93aa">PTY SSH RELAY</p></div>
</div>
<p>This host is an SSH relay. Internal agents install themselves with the
one-line command printed by <code>add-server</code> in the C2 CLI.</p>
<p>Endpoints: <code>/health</code>, <code>/agent.tar.gz</code>,
<code>/install/&lt;server-id&gt;?token=&lt;token&gt;</code></p>
</body></html>
"""

# The bootstrap is generated per request.  Placeholders use an unusual marker
# so they never collide with bash syntax.
_BOOTSTRAP = """#!/usr/bin/env bash
#
# WS-SSH agent installer (generated).
#   curl -fsSL '@@URL@@' | sudo bash
#
set -euo pipefail

WS_RELAY_URL=@@RELAY_URL@@
WS_SERVER_ID=@@SERVER_ID@@
WS_AGENT_TOKEN=@@TOKEN@@
WS_SHELL=''
WS_PACKAGE_URL=@@PACKAGE_URL@@
WS_INSTALL_DIR=@@INSTALL_DIR@@
export WS_RELAY_URL WS_SERVER_ID WS_AGENT_TOKEN WS_SHELL WS_PACKAGE_URL WS_INSTALL_DIR

if [ "$(id -u)" -ne 0 ] && [ "${WS_ALLOW_NONROOT:-0}" != "1" ]; then
  echo "[ws] must run as root (use: curl ... | sudo bash)" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y >/dev/null
    apt-get install -y --no-install-recommends curl ca-certificates >/dev/null
  else
    echo "[ws] curl is required" >&2
    exit 1
  fi
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "[ws] downloading agent package..."
curl -fsSL "$WS_PACKAGE_URL" -o "$TMP/agent.tar.gz"
tar -xzf "$TMP/agent.tar.gz" -C "$TMP"

if [ ! -f "$TMP/agent/setup.sh" ]; then
  echo "[ws] agent package is missing agent/setup.sh" >&2
  exit 1
fi

exec bash "$TMP/agent/setup.sh"
"""

DEFAULT_AGENT_DIR = "/opt/ws-ssh-agent"


def _sh_single(value: str) -> str:
    """Quote *value* for a single-quoted bash word."""
    return "'" + value.replace("'", "'\\''") + "'"


def _dq_escape(value: str) -> str:
    """Escape *value* for use inside a double-quoted bash string."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")


def _host_from_header(host_header: str) -> str:
    host_header = (host_header or "").strip()
    if host_header.startswith("["):
        return host_header.split("]", 1)[0] + "]"
    return host_header.split(":", 1)[0]


