#!/bin/bash
# Installe zelda-watch sur ton Mac et le fait tourner en continu (redémarre tout seul à l'ouverture de session).
#
#   ./setup_mac.sh          → surveille Interdiscount et Fnac (les sites qui refusent GitHub)
#   ./setup_mac.sh tout     → surveille toutes les boutiques
#   ./setup_mac.sh stop     → arrête et retire le service
#
# Log en direct : tail -f zelda.log
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="ch.vergasta.zeldawatch"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
MODE="${1:-}"

if [[ "$MODE" == "stop" ]]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "⏹  Surveillance arrêtée."
  exit 0
fi

cd "$DIR"

echo "▶ Installation de Python + Playwright…"
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip playwright
.venv/bin/python -m playwright install chromium firefox

[[ -f .env ]] || cp .env.example .env

# Quelles boutiques ce Mac surveille
sed -i '' '/^ONLY_SITES=/d' .env
if [[ "$MODE" != "tout" ]]; then
  echo "ONLY_SITES=Interdiscount,Fnac" >> .env
fi

if ! grep -qE '^(NTFY_TOPIC|TELEGRAM_BOT_TOKEN)=.+' .env; then
  echo
  echo "⚠️  Aucune notification configurée."
  echo "   Ouvre $DIR/.env, remplis NTFY_TOPIC ou TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, puis relance ./setup_mac.sh"
  exit 1
fi

echo
echo "▶ Passage de test depuis ta connexion (1–2 min)…"
.venv/bin/python zelda_watch.py --test || true

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <!-- caffeinate -s : empêche la mise en veille tant que le Mac est sur secteur -->
    <string>/usr/bin/caffeinate</string><string>-s</string>
    <string>$DIR/.venv/bin/python</string>
    <string>$DIR/zelda_watch.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/zelda.log</string>
  <key>StandardErrorPath</key><string>$DIR/zelda.log</string>
</dict></plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo
echo "✅ Surveillance lancée en arrière-plan ($( [[ "$MODE" == "tout" ]] && echo "toutes les boutiques" || echo "Interdiscount + Fnac" ))."
echo "   Log en direct : tail -f \"$DIR/zelda.log\""
echo "   Arrêter       : ./setup_mac.sh stop"
