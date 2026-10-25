#!/bin/bash
# Remove the per-user LaunchAgent. Your config.yaml and .env are left in place.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This uninstaller is for macOS." >&2
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run this as your normal login user, not with sudo." >&2
  exit 1
fi

UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"
LABEL="com.jarvis.assistant"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
if [[ -f "$DEST" ]]; then
  # Older macOS used unload. Ignore failure if bootstrap already removed it.
  launchctl unload "$DEST" 2>/dev/null || true
  rm -f "$DEST"
fi

echo "Removed the Jarvis login service."
echo "Log files are still in $HOME/Library/Logs/jarvis/ if you want them."
