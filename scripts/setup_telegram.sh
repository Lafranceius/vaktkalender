#!/usr/bin/env bash
# Connect a Telegram bot so the Pi can message you. Run on the Mac:  ./scripts/setup_telegram.sh
set -euo pipefail
# The Pi's ssh name is in local.env (never committed), e.g. PI=raspberrypi
PI="${PI:-$(sed -n 's/^PI=//p' "$(dirname "$0")/../local.env" 2>/dev/null || true)}"
: "${PI:?Sett PI=<ssh-navnet til Pi-en> i local.env}"
API="https://api.telegram.org/bot"

echo
echo "── 1/3  Lag boten ─────────────────────────────────────────────"
echo "  • I Telegram: åpne chatten med @BotFather (åpnes nå) og send  /newbot"
echo "  • Navn: Vaktkalender"
echo "  • Brukernavn: noe som slutter på «bot», f.eks. vaktkalender_dittnavn_bot"
echo "  • BotFather svarer med et token (ser ut som 123456789:AAH...). Kopier det."
open "https://t.me/BotFather" 2>/dev/null || true
echo
while true; do
  read -rsp "  Lim inn tokenet (vises ikke mens du limer): " TOKEN
  echo
  if BOT=$(curl -fsS "${API}${TOKEN}/getMe" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["username"])' 2>/dev/null); then
    echo "  ✓ Fant boten @$BOT"
    break
  fi
  echo "  ⚠ Telegram godtok ikke tokenet. Prøv igjen."
done

echo
echo "── 2/3  Si hei til boten ──────────────────────────────────────"
echo "  • Chatten med @$BOT åpnes nå. Trykk Start og send «hei»."
open "https://t.me/$BOT" 2>/dev/null || true
CHAT=""
while [[ -z "$CHAT" ]]; do
  read -rp "  Trykk Enter når du har sendt «hei» … " _
  CHAT=$(curl -fsS "${API}${TOKEN}/getUpdates" | python3 -c '
import json, sys
updates = json.load(sys.stdin)["result"]
print(next((u["message"]["chat"]["id"] for u in reversed(updates) if "message" in u), ""))' || true)
  [[ -n "$CHAT" ]] || echo "  ⚠ Fant ingen melding ennå. Send «hei» til @$BOT og prøv igjen."
done
echo "  ✓ Fant chatten din"

echo
echo "── 3/3  Lagre på Pi-en og test ───────────────────────────────"
# The token travels over ssh stdin, never as a command-line argument.
printf '%s\n%s\n' "$TOKEN" "$CHAT" | ssh "$PI" 'python3 -c "
import os, re, sys
token, chat = sys.stdin.read().split()
path = os.path.expanduser(\"~/.config/vaktkalender/config.env\")
text = open(path).read()
for key, value in ((\"TELEGRAM_BOT_TOKEN\", token), (\"TELEGRAM_CHAT_ID\", chat)):
    line = f\"{key}={value}\"
    text = re.sub(rf\"^{key}=.*$\", line, text, flags=re.M) if re.search(rf\"^{key}=\", text, re.M) else text.rstrip() + \"\\n\" + line + \"\\n\"
open(path, \"w\").write(text)
os.chmod(path, 0o600)
"'
ssh "$PI" 'systemctl --user restart vaktkalender-web'
echo "  ✓ Lagret"
if OUT=$(ssh "$PI" 'cd ~/vaktkalender/app && set -a && . ~/.config/vaktkalender/config.env && set +a && ../.venv/bin/python -m vaktkalender test-telegram' 2>&1); then
  echo "  ✓ Testmelding sendt. Du skal nå ha fått «✅ Vaktkalender kan sende meldinger til deg.» i Telegram."
else
  echo "  ⚠ Telegram tok ikke imot testmeldingen:"
  printf '%s\n' "$OUT" | sed 's/^/    /'
  exit 1
fi
echo
