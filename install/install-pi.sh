#!/usr/bin/env bash
# Install the job agent on a Raspberry Pi as a systemd timer.
#   scp -r job-agent pi@raspberrypi.local:~/
#   ssh pi@raspberrypi.local 'bash ~/job-agent/install/install-pi.sh'
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(id -un)"
PY="$APP_DIR/.venv/bin/python"

# Unit name defaults to the install directory, so two instances for two people can
# live on one machine without fighting over the same systemd unit.
UNIT="${UNIT_NAME:-$(basename "$APP_DIR")}"
# Weekday morning by default. Override with SCAN_TIME=07:35 to stagger instances.
WHEN="${SCAN_TIME:-07:15}"

echo "Installing $UNIT from $APP_DIR (runs weekdays at $WHEN)"

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

sudo tee "/etc/systemd/system/$UNIT.service" >/dev/null <<UNITFILE
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
UNITFILE

sudo tee "/etc/systemd/system/$UNIT.timer" >/dev/null <<UNITFILE
[Unit]
Description=Run $UNIT every weekday morning

[Timer]
OnCalendar=Mon..Fri $WHEN
Persistent=true
RandomizedDelaySec=600

[Install]
WantedBy=timers.target
UNITFILE

sudo systemctl daemon-reload
sudo systemctl enable --now "$UNIT.timer"

echo
echo "Installed. Next:"
echo "  1. nano $APP_DIR/.env          fill in SMTP_USER, SMTP_PASSWORD, MAIL_TO"
echo "  2. $PY $APP_DIR/run.py test-email"
echo "  3. $PY $APP_DIR/run.py scan --dry-run"
echo
echo "  systemctl list-timers $UNIT.timer     when it next fires"
echo "  sudo systemctl start $UNIT.service    run it right now"
echo "  tail -f $APP_DIR/out/agent.log           watch it work"
