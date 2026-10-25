#!/bin/bash
# Install a per-user LaunchAgent so Jarvis starts at login and restarts after a crash.
# Run this as your normal user, from the project directory's scripts folder or via the path below.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS. On this machine, try: python -m jarvis demo" >&2
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run this as your normal login user, not with sudo." >&2
  echo "A root service cannot use your microphone or your Bluetooth speaker." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${JARVIS_PYTHON:-$ROOT/.venv/bin/python}"
TEMPLATE="$ROOT/launchd/com.jarvis.assistant.plist.template"
DEST="$HOME/Library/LaunchAgents/com.jarvis.assistant.plist"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"
LABEL="com.jarvis.assistant"

if [[ ! -x "$PYTHON" ]]; then
  echo "Could not find an executable Python at:" >&2
  echo "  $PYTHON" >&2
  echo "Create the virtualenv first (see the README), or set JARVIS_PYTHON." >&2
  exit 1
fi

if [[ ! -f "$ROOT/config.yaml" ]]; then
  echo "Missing $ROOT/config.yaml" >&2
  exit 1
fi

if [[ ! -f "$ROOT/.env" ]]; then
  echo "Note: $ROOT/.env does not exist yet. Conversations need an API key in that file."
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/jarvis"
sed \
  -e "s|__PYTHON__|$PYTHON|g" \
  -e "s|__ROOT__|$ROOT|g" \
  -e "s|__HOME__|$HOME|g" \
  "$TEMPLATE" > "$DEST"

# Replace a previous copy of this agent if it is already loaded.
launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$DEST"
launchctl enable "${DOMAIN}/${LABEL}"
launchctl kickstart -k "${DOMAIN}/${LABEL}"

echo "Installed ${DEST}"
echo "Jarvis is running in your login session and will start again at login."
echo "Logs: $HOME/Library/Logs/jarvis/"
echo "Status: launchctl print ${DOMAIN}/${LABEL}"
echo "Stop:   $ROOT/scripts/uninstall_launchd.sh"
