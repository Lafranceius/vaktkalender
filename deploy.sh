#!/usr/bin/env bash
# Copy the app to the Pi over Tailscale and (re)start it.
set -euo pipefail
cd "$(dirname "$0")"
# The Pi's ssh name is in local.env (never committed), e.g. PI=raspberrypi
PI="${PI:-$(sed -n 's/^PI=//p' local.env 2>/dev/null || true)}"
: "${PI:?Sett PI=<ssh-navnet til Pi-en> i local.env}"

rsync -az --delete \
  --exclude .venv --exclude .git --exclude __pycache__ --exclude .pytest_cache --exclude '*.db' --exclude local.env \
  ./ "$PI:vaktkalender/app/"

ssh "$PI" 'set -e
  cd ~/vaktkalender
  [ -x .venv/bin/python ] || python3 -m venv .venv
  .venv/bin/pip install -q --disable-pip-version-check -r app/requirements.txt
  mkdir -p ~/.config/systemd/user ~/.config/vaktkalender ~/.local/share/vaktkalender
  chmod 700 ~/.config/vaktkalender
  if [ ! -f ~/.config/vaktkalender/config.env ]; then
    install -m 600 app/config.example.env ~/.config/vaktkalender/config.env
    echo "Laget ~/.config/vaktkalender/config.env – fyll inn verdiene."
  fi
  cp app/systemd/*.service app/systemd/*.timer ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now vaktkalender-run.timer >/dev/null
  systemctl --user enable vaktkalender-web.service >/dev/null
  systemctl --user restart vaktkalender-web.service
  sleep 3
  curl -fsS http://127.0.0.1:8090/healthz >/dev/null && echo "✅ Web kjører på Pi-en (127.0.0.1:8090)"
'
