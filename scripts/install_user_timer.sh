#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"

sed "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" \
  "$PROJECT_ROOT/deploy/paper-digest.service" \
  > "$SYSTEMD_USER_DIR/paper-digest.service"

cp "$PROJECT_ROOT/deploy/paper-digest.timer" \
  "$SYSTEMD_USER_DIR/paper-digest.timer"

chmod 644 "$SYSTEMD_USER_DIR/paper-digest.service"
chmod 644 "$SYSTEMD_USER_DIR/paper-digest.timer"
systemctl --user daemon-reload
systemctl --user enable --now paper-digest.timer

if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "$USER" || {
    echo "Warning: could not enable lingering; enable it manually for headless use." >&2
  }
fi

echo "Installed paper-digest.timer for user $USER"
systemctl --user status paper-digest.timer --no-pager || true