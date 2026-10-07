#!/usr/bin/env bash
#
# setup-relay.sh - install the WS-SSH *relay server* on Ubuntu.
#
# Creates a Python virtualenv, writes a config file, initialises the SQLite
# registry and installs a systemd service. Safe to re-run (idempotent).
#
#   sudo ./relay/setup.sh --admin-user alice
#   sudo ./relay/setup.sh --ssh-port 2222 --ws-port 8765 --admin-user alice --admin-password s3cret
#
set -euo pipefail

# --------------------------------------------------------------------------- #
# defaults
# --------------------------------------------------------------------------- #
SSH_HOST="0.0.0.0"
SSH_PORT="2222"
WS_HOST="0.0.0.0"
WS_PORT="8765"
WEB_HOST="0.0.0.0"
WEB_PORT="1234"
ADVERTISE_HOST=""
INSTALL_DIR="/opt/ws-ssh-relay"
CONFIG_DIR="/etc/ws-ssh"
DATA_DIR="/var/lib/ws-ssh"
SERVICE_NAME="ws-relay"
SERVICE_USER="root"
ADMIN_USER=""
ADMIN_PASSWORD=""
INSTALL_SERVICE=1
SKIP_DEPS=0
ALLOW_NONROOT=0

# --------------------------------------------------------------------------- #
# pretty logging
# --------------------------------------------------------------------------- #
C_RESET=$'\033[0m'; C_INFO=$'\033[1;34m'; C_OK=$'\033[1;32m'; C_WARN=$'\033[1;33m'; C_ERR=$'\033[1;31m'
log()  { printf '%s[relay]%s %s\n' "$C_INFO" "$C_RESET" "$*"; }
ok()   { printf '%s[ ok ]%s %s\n'  "$C_OK"   "$C_RESET" "$*"; }
warn() { printf '%s[warn]%s %s\n'  "$C_WARN" "$C_RESET" "$*" >&2; }
die()  { printf '%s[fail]%s %s\n'  "$C_ERR"  "$C_RESET" "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: sudo ./relay/setup.sh [options]

Options:
  --ssh-host HOST        SSH bind address          (default: 0.0.0.0)
  --ssh-port PORT        SSH port                  (default: 2222)
  --ws-host HOST         Agent WebSocket bind      (default: 0.0.0.0)
  --ws-port PORT         Agent WebSocket port      (default: 8765)
  --web-host HOST        installer web bind        (default: 0.0.0.0)
  --web-port PORT        installer web port        (default: 1234)
  --advertise-host HOST  public host for install URLs (default: auto)
  --install-dir DIR      application install dir   (default: /opt/ws-ssh-relay)
  --config-dir DIR       config dir                (default: /etc/ws-ssh)
  --data-dir DIR         database / host key dir   (default: /var/lib/ws-ssh)
  --service-name NAME    systemd unit name         (default: ws-relay)
  --service-user USER    run the service as USER   (default: root)
  --admin-user NAME      create this relay SSH user
  --admin-password PASS  password for --admin-user (omit to be prompted)
  --no-service           do not install/start systemd service
  --skip-deps            skip apt-get package installation
  --allow-nonroot        install into a user-writable dir without root
  -h, --help             show this help
EOF
}

