#!/usr/bin/env bash
# Install the job agent on a Raspberry Pi as a systemd timer.
#   scp -r job-agent pi@raspberrypi.local:~/
#   ssh pi@raspberrypi.local 'bash ~/job-agent/install/install-pi.sh'
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(id -un)"
PY="$APP_DIR/.venv/bin/python"

echo "Installing job agent from $APP_DIR"

if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "Created .env from the template. Fill it in before the first run."
fi
# Credentials live here in plaintext. Owner-read-only, always.
chmod 600 "$APP_DIR/.env"

python3 -m venv "$APP_DIR/.venv"
"$PY" -m pip install --quiet --upgrade pip
# The scan itself is pure stdlib. anthropic is only needed for the scoring pass.
"$PY" -m pip install --quiet anthropic

mkdir -p "$APP_DIR/out"

sudo tee /etc/systemd/system/jobagent.service >/dev/null <<UNIT
[Unit]
Description=Job search agent scan
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER_NAME
WorkingDirectory=$APP_DIR
ExecStart=$PY $APP_DIR/run.py scan --skip-empty
StandardOutput=append:$APP_DIR/out/agent.log
StandardError=append:$APP_DIR/out/agent.log
# Basic containment: the agent only needs its own directory.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$APP_DIR/out
UNIT

sudo tee /etc/systemd/system/jobagent.timer >/dev/null <<'UNIT'
[Unit]
Description=Run the job search agent every weekday morning

[Timer]
OnCalendar=Mon..Fri 07:15
Persistent=true
RandomizedDelaySec=600

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now jobagent.timer

echo
echo "Installed. Next:"
echo "  1. nano $APP_DIR/.env          fill in SMTP_USER, SMTP_PASSWORD, MAIL_TO"
echo "  2. $PY $APP_DIR/run.py test-email"
echo "  3. $PY $APP_DIR/run.py scan --dry-run"
echo
echo "  systemctl list-timers jobagent.timer     when it next fires"
echo "  sudo systemctl start jobagent.service    run it right now"
echo "  tail -f $APP_DIR/out/agent.log           watch it work"
