#!/usr/bin/env bash
# One-time setup on Ubuntu 22.04/24.04.  Run:  ./setup.sh
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"

echo "==> Installing system packages (ffmpeg, fonts, python)"
sudo apt-get update -y
sudo apt-get install -y ffmpeg python3-venv python3-pip fonts-dejavu-core fonts-noto-core

echo "==> Creating Python environment"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install --upgrade yt-dlp streamlink   # these change often, always get the newest

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> Created .env  (edit it: nano .env)"
fi

echo "==> Installing background service 'gta6clipper' (auto-restarts if it crashes)"
sudo tee /etc/systemd/system/gta6clipper.service >/dev/null <<UNIT
[Unit]
Description=GTA 6 live stream clipper
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python -m clipper run
Restart=always
RestartSec=10
User=$(whoami)
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable gta6clipper >/dev/null

echo
echo "Done! Next steps:"
echo "  1) nano .env                                  (add your keys)"
echo "  2) .venv/bin/python -m clipper chatid         (get TELEGRAM_CHAT_ID)"
echo "  3) .venv/bin/python -m clipper selftest       (check everything works)"
echo "  4) sudo systemctl start gta6clipper           (start it)"
echo "     journalctl -u gta6clipper -f               (watch the log)"
