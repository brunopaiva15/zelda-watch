#!/bin/bash
# Installe zelda-watch sur un serveur Debian/Ubuntu et le fait tourner en service systemd (24/7, redémarre tout seul).
#
#   ./setup_linux.sh test    → installe et fait juste un passage de test sur Interdiscount + Fnac (rien n'est lancé en continu)
#   ./setup_linux.sh         → service qui surveille Interdiscount + Fnac
#   ./setup_linux.sh tout    → service qui surveille toutes les boutiques
#   ./setup_linux.sh stop    → arrête et retire le service
#
# Log en direct : journalctl -u zelda-watch -f
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE="zelda-watch"
UNIT="/etc/systemd/system/$SERVICE.service"
MODE="${1:-}"
SUDO=""; [[ $EUID -ne 0 ]] && SUDO="sudo"
RUN_USER="$(id -un)"

if [[ "$MODE" == "stop" ]]; then
  $SUDO systemctl disable --now "$SERVICE" 2>/dev/null || true
  $SUDO rm -f "$UNIT"
  $SUDO systemctl daemon-reload
  echo "⏹  Surveillance arrêtée."
  exit 0
fi

cd "$DIR"

echo "▶ Paquets système…"
if ! python3 -m venv --help >/dev/null 2>&1 || ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq python3-venv python3-pip
fi

echo "▶ Python + Playwright…"
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip playwright
$SUDO .venv/bin/python -m playwright install-deps chromium firefox
.venv/bin/python -m playwright install chromium firefox

[[ -f .env ]] || cp .env.example .env
sed -i 's/\r$//' .env    # au cas où le fichier vient de Windows

if ! grep -qE '^(NTFY_TOPIC|TELEGRAM_BOT_TOKEN)=.+' .env; then
  echo
  echo "⚠️  Aucune notification configurée."
  echo "   nano $DIR/.env   → remplis NTFY_TOPIC ou TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, puis relance ce script."
  exit 1
fi

if [[ "$MODE" == "test" ]]; then
  echo
  echo "▶ Passage de test sur Interdiscount + Fnac depuis ce serveur (1–3 min)…"
  ONLY_SITES=Interdiscount,Fnac .venv/bin/python zelda_watch.py --once
  echo "Si Interdiscount et Fnac affichent ❌ rupture ou ✅ EN STOCK (et pas 🚫 bloqué), lance : ./setup_linux.sh"
  exit 0
fi

# Quelles boutiques ce serveur surveille
sed -i '/^ONLY_SITES=/d' .env
if [[ "$MODE" != "tout" ]]; then
  echo "ONLY_SITES=Interdiscount,Fnac" >> .env
fi

echo
echo "▶ Passage de test (1–3 min)…"
.venv/bin/python zelda_watch.py --test || true

echo
echo "▶ Service systemd…"
$SUDO tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=zelda-watch — alerte stock manette Pro Switch 2 Zelda
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python $DIR/zelda_watch.py
Restart=always
RestartSec=30
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now "$SERVICE"
$SUDO systemctl restart "$SERVICE"

echo
echo "✅ Service lancé ($( [[ "$MODE" == "tout" ]] && echo "toutes les boutiques" || echo "Interdiscount + Fnac" ))."
echo "   Log en direct : journalctl -u $SERVICE -f"
echo "   Statut        : systemctl status $SERVICE"
echo "   Arrêter       : ./setup_linux.sh stop"
