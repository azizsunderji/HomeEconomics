#!/usr/bin/env bash
# Install/refresh the editor's user-level systemd units on the droplet.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.config/systemd/user ~/work/noon/logs
cp "$HERE"/systemd/*.service "$HERE"/systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now noon-editor.service noon-ingest.timer noon-send.timer
systemctl --user restart noon-editor.service
systemctl --user list-timers --all | grep -E "noon|NEXT" || true
systemctl --user status noon-editor.service --no-pager | head -5
