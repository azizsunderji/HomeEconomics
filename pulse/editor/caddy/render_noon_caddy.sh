#!/usr/bin/env bash
# Install noon.caddy into /etc/caddy/conf.d with the private path token filled in, then reload Caddy.
# The token lives only in ~/.noon_env (NOON_PRIVATE_TOKEN) and the GitHub secret of the same name;
# the repo copy keeps the placeholder {$NOON_PRIVATE_TOKEN}. Nothing here prints the token.
# Usage: pulse/editor/caddy/render_noon_caddy.sh   (needs sudo; run as aziz)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
env_file="${NOON_ENV_FILE:-/home/aziz/.noon_env}"
token="$(grep -m1 '^NOON_PRIVATE_TOKEN=' "$env_file" | cut -d= -f2- | tr -d '[:space:]')"
if ! [[ "$token" =~ ^[0-9a-f]{32}$ ]]; then
  echo "NOON_PRIVATE_TOKEN missing or not 32 hex characters in $env_file; nothing installed" >&2
  exit 1
fi
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
sed "s|{\$NOON_PRIVATE_TOKEN}|$token|g" "$here/noon.caddy" > "$tmp"
if grep -q 'NOON_PRIVATE_TOKEN}' "$tmp"; then
  echo "placeholder not replaced; nothing installed" >&2
  exit 1
fi
sudo install -o root -g caddy -m 640 "$tmp" /etc/caddy/conf.d/noon.caddy
sudo caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 || {
  echo "caddy validate failed; run: sudo caddy validate --config /etc/caddy/Caddyfile" >&2
  exit 1
}
sudo systemctl reload caddy
echo "installed /etc/caddy/conf.d/noon.caddy and reloaded caddy"
