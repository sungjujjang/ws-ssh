#!/usr/bin/env bash
#
# setup-agent.sh - install the WS-SSH *internal agent* on Ubuntu.
#
# The agent dials OUT to the relay (no inbound firewall rule needed), runs the
# user's shell inside a real PTY and streams bytes bidirectionally.
# Safe to re-run (idempotent); existing credentials are preserved unless
# overridden with --relay/--id/--token.
#
#   sudo ./agent/setup.sh \
#       --relay ws://relay.example.com:8765/agent \
#       --id server-001 --token ws_xxxxxxxx
#
# It also accepts the same values from the WS_* environment variables, which is
# how the relay's one-line installer (curl ... | sudo bash) drives it.
#
set -euo pipefail

# --------------------------------------------------------------------------- #
# defaults (environment gives the one-line installer a head start)
# --------------------------------------------------------------------------- #
RELAY_URL="${WS_RELAY_URL:-}"
SERVER_ID="${WS_SERVER_ID:-}"
TOKEN="${WS_AGENT_TOKEN:-}"
SHELL_PATH="${WS_SHELL:-}"
INSTALL_DIR="${WS_INSTALL_DIR:-/opt/ws-ssh-agent}"
CONFIG_DIR="${WS_CONFIG_DIR:-/etc/ws-ssh}"
SERVICE_NAME="ws-agent"
SERVICE_USER="root"
INSTALL_SERVICE=1
SKIP_DEPS=0
ALLOW_NONROOT="${WS_ALLOW_NONROOT:-0}"

# --------------------------------------------------------------------------- #
# pretty logging
# --------------------------------------------------------------------------- #
C_RESET=$'\033[0m'; C_INFO=$'\033[1;34m'; C_OK=$'\033[1;32m'; C_WARN=$'\033[1;33m'; C_ERR=$'\033[1;31m'
log()  { printf '%s[agent]%s %s\n' "$C_INFO" "$C_RESET" "$*"; }
ok()   { printf '%s[ ok ]%s %s\n'  "$C_OK"   "$C_RESET" "$*"; }
warn() { printf '%s[warn]%s %s\n'  "$C_WARN" "$C_RESET" "$*" >&2; }
die()  { printf '%s[fail]%s %s\n'  "$C_ERR"  "$C_RESET" "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: sudo ./agent/setup.sh --relay URL --id ID --token TOKEN [options]

Values may also be supplied via WS_RELAY_URL, WS_SERVER_ID, WS_AGENT_TOKEN,
WS_SHELL and WS_INSTALL_DIR (used by the relay's one-line installer).

Options:
  --relay URL            relay WebSocket URL, e.g. ws://relay:8765/agent
  --id ID                server id registered on the relay (e.g. server-001)
  --token TOKEN          agent registration token (ws_...)
  --shell PATH           shell to run (default: platform default, e.g. /bin/bash)
  --install-dir DIR      application install dir   (default: /opt/ws-ssh-agent)
  --config-dir DIR       config dir                (default: /etc/ws-ssh)
  --service-name NAME    systemd unit name         (default: ws-agent)
  --service-user USER    run the agent as USER     (default: root)
  --no-service           do not install/start systemd service
  --skip-deps            skip apt-get package installation
  --allow-nonroot        install into a user-writable dir without root
  -h, --help             show this help
EOF
}

# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #
while [ $# -gt 0 ]; do
  case "$1" in
    --relay)           RELAY_URL="${2:?}"; shift 2 ;;
    --id)              SERVER_ID="${2:?}"; shift 2 ;;
    --token)           TOKEN="${2:?}"; shift 2 ;;
    --shell)           SHELL_PATH="${2:?}"; shift 2 ;;
    --install-dir)     INSTALL_DIR="${2:?}"; shift 2 ;;
    --config-dir)      CONFIG_DIR="${2:?}"; shift 2 ;;
    --service-name)    SERVICE_NAME="${2:?}"; shift 2 ;;
    --service-user)    SERVICE_USER="${2:?}"; shift 2 ;;
    --no-service)      INSTALL_SERVICE=0; shift ;;
    --skip-deps)       SKIP_DEPS=1; shift ;;
    --allow-nonroot)   ALLOW_NONROOT=1; shift ;;
    -h|--help)         usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

VENV="$INSTALL_DIR/venv"
PY="$VENV/bin/python"
ENV_FILE="$CONFIG_DIR/agent.env"

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    if [ "$ALLOW_NONROOT" = "1" ]; then
      warn "running as non-root (--allow-nonroot): system packages and services are disabled"
      INSTALL_SERVICE=0
      SKIP_DEPS=1
      if [ "$CONFIG_DIR" = "/etc/ws-ssh" ]; then
        CONFIG_DIR="$INSTALL_DIR/etc"
      fi
      ENV_FILE="$CONFIG_DIR/agent.env"
      return
    fi
    die "must run as root (try: sudo $0 $*) or pass --allow-nonroot for a user-local install"
  fi
}

detect_os() {
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}${ID_LIKE:-}" in
      *ubuntu*|*debian*|*linuxmint*) : ;;
      *) warn "this script targets Ubuntu/Debian but ID='${ID:-unknown}' was detected; continuing" ;;
    esac
  fi
}

install_deps() {
  if [ "$SKIP_DEPS" = "1" ]; then
    log "skipping apt-get (--skip-deps)"
    return
  fi
  log "installing system packages (python3, venv, pip)"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip ca-certificates
}

repo_root() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  echo "$(cd "$here/.." && pwd)"
}

