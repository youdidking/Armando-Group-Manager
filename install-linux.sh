#!/usr/bin/env bash
# Docker-free installer/updater for a Debian/Ubuntu Linux VPS.
set -Eeuo pipefail

APP_DIR="/opt/armando"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this installer with sudo: sudo bash $0" >&2
  exit 1
fi
if [[ "$SCRIPT_DIR" != "$APP_DIR" ]]; then
  echo "Extract the project into $APP_DIR first, then run: sudo bash $APP_DIR/install-linux.sh" >&2
  exit 1
fi
if [[ ! -f "$APP_DIR/requirements.txt" || ! -f "$APP_DIR/deploy/armando.service" ]]; then
  echo "Project files are missing. Clone the complete Git repository into $APP_DIR first." >&2
  exit 1
fi
if [[ ! -f /etc/debian_version ]]; then
  echo "This installer supports Debian/Ubuntu-based servers. For easiest setup, use Ubuntu 24.04 LTS." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git ca-certificates ffmpeg libgl1 libglib2.0-0 libgomp1 libsm6 libxext6 libxrender1

# Prefer the repository's Python 3.12 runtime; accept newer supported Python versions too.
PYTHON_BIN=""
for candidate in python3.12 python3.13 python3.14 python3; do
  if command -v "$candidate" >/dev/null 2>&1 \
    && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    PYTHON_BIN="$(command -v "$candidate")"
    break
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python 3.12+ was not found. Installing Python 3.12 from the configured apt repositories..."
  apt-get install -y python3.12 python3.12-venv
  PYTHON_BIN="$(command -v python3.12)"
fi

PYTHON_MINOR="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
apt-get install -y "python${PYTHON_MINOR}-venv"

if ! getent group armando >/dev/null 2>&1; then
  groupadd --system armando
fi
if ! id armando >/dev/null 2>&1; then
  useradd --system --gid armando --home-dir "$APP_DIR" --shell /usr/sbin/nologin armando
fi

# Keep application source and .git non-writable by the bot account.
chown -R root:armando "$APP_DIR"
mkdir -p "$APP_DIR/data" "$APP_DIR/logs"
chown -R armando:armando "$APP_DIR/data" "$APP_DIR/logs"
chmod 0750 "$APP_DIR/data" "$APP_DIR/logs"
if [[ ! -f "$APP_DIR/.env" ]]; then
  install -o armando -g armando -m 0600 "$APP_DIR/.env.example" "$APP_DIR/.env"
else
  chown armando:armando "$APP_DIR/.env"
fi
chmod 0600 "$APP_DIR/.env"
if [[ -d "$APP_DIR/.venv" ]]; then
  chown -R armando:armando "$APP_DIR/.venv"
fi

if [[ ! -x "$APP_DIR/.venv/bin/python" ]] \
  || ! runuser -u armando -- "$APP_DIR/.venv/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
  rm -rf "$APP_DIR/.venv"
  runuser -u armando -- "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
fi
export PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1
runuser -u armando -- "$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
runuser -u armando -- "$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"
runuser -u armando -- "$APP_DIR/.venv/bin/python" -m pip check

install -m 0644 "$APP_DIR/deploy/armando.service" /etc/systemd/system/armando.service
systemctl daemon-reload

if grep -Eq '^BOT_TOKEN=[^[:space:]]+' "$APP_DIR/.env"; then
  systemctl enable armando.service
  if systemctl is-active --quiet armando.service; then
    systemctl restart armando.service
  else
    systemctl start armando.service
  fi
  echo "Armando is installed and running. Check: systemctl status armando"
else
  echo "Dependencies and systemd service installed. Set BOT_TOKEN in $APP_DIR/.env, then run:"
  echo "  sudo systemctl enable --now armando"
fi
