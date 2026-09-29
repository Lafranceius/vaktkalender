#!/usr/bin/env bash
# Run on the Mac when Telegram says the RBU login has expired (about every 90 days).
set -euo pipefail
cd "$(dirname "$0")"
# The Pi's ssh name is in local.env (never committed), e.g. PI=raspberrypi
PI="${PI:-$(sed -n 's/^PI=//p' local.env 2>/dev/null || true)}"
: "${PI:?Sett PI=<ssh-navnet til Pi-en> i local.env}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# RBU's addresses are only in the Pi's config, not in this repo.
pi_setting() { ssh "$PI" "sed -n 's/^$1=//p' ~/.config/vaktkalender/config.env" | tr -d "\"'"; }
export RBU_APP_URL="$(pi_setting RBU_APP_URL)" RBU_API_URL="$(pi_setting RBU_API_URL)"
[[ -n "$RBU_APP_URL" && -n "$RBU_API_URL" ]] || { echo "Fant ikke RBU_APP_URL/RBU_API_URL i config.env på $PI."; exit 1; }

uv run --quiet --no-project --python 3.13 --with playwright==1.63.0 \
  python -m vaktkalender login --out "$TMP/storage_state.json"

scp -q "$TMP/storage_state.json" "$PI:.config/vaktkalender/storage_state.json"
ssh "$PI" 'chmod 600 ~/.config/vaktkalender/storage_state.json && systemctl --user start --no-block vaktkalender-run.service'
echo "✅ Ny RBU-innlogging er lagt på Pi-en, og en ny henting er startet."
