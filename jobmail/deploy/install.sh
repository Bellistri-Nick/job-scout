#!/usr/bin/env bash
# Install jobmail on a Raspberry Pi (Pi OS / Debian). Run from the repo root:
#   sudo bash deploy/install.sh [service-user]
set -euo pipefail

USER_NAME="${1:-${SUDO_USER:-pi}}"
DEST=/opt/jobmail
SRC="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Installing to $DEST as user $USER_NAME"
apt-get install -y python3-venv >/dev/null
mkdir -p "$DEST"
rsync -a --delete --exclude '.venv' --exclude '.env' --exclude '__pycache__' --exclude '.git' "$SRC/" "$DEST/"

if [ ! -d "$DEST/.venv" ]; then
  python3 -m venv "$DEST/.venv"
fi
"$DEST/.venv/bin/pip" install --upgrade pip >/dev/null
"$DEST/.venv/bin/pip" install -e "$DEST" >/dev/null

if [ ! -f "$DEST/.env" ]; then
  cp "$DEST/.env.example" "$DEST/.env"
  echo "!!  Created $DEST/.env from the example — edit it before starting the timer."
fi
chown -R "$USER_NAME":"$USER_NAME" "$DEST"
chmod 600 "$DEST/.env"

# A .env authored on Windows is CRLF. systemd's EnvironmentFile does NOT strip
# the trailing carriage return, so every value silently gains one and the
# credentials fail -- while still working when you run the pipeline by hand,
# because python-dotenv does strip it. That split behaviour is miserable to
# debug, so repair it here rather than documenting it.
# tr parses the \015 escape itself, so no carriage return ever has to survive a
# round trip through shell quoting -- which is exactly where this kept breaking.
if ! tr -d '\015' < "$DEST/.env" | cmp -s - "$DEST/.env"; then
  tr -d '\015' < "$DEST/.env" > "$DEST/.env.tmp"
  mv "$DEST/.env.tmp" "$DEST/.env"
  chmod 600 "$DEST/.env"
  echo "!!  Stripped Windows line endings from $DEST/.env"
fi

# systemd units: templated on the service user via %i
for unit in jobmail-poll.service jobmail-poll.timer jobmail-dashboard.service; do
  sed "s/User=%i/User=$USER_NAME/" "$DEST/deploy/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now jobmail-dashboard.service
systemctl enable jobmail-poll.timer
# enable --now will not restart an already-running unit, so an upgrade needs this
systemctl restart jobmail-dashboard.service

PORT="$(grep -E '^DASHBOARD_PORT=' "$DEST/.env" 2>/dev/null | cut -d= -f2)"
PORT="${PORT:-8080}"
IP="$(hostname -I | awk '{print $1}')"

cat <<MSG

Installed. Next:
  1. Put your .env in place. Copy one you already filled in rather than
     retyping it, then re-run this installer: it strips Windows line endings
     automatically, which systemd will not do for you.
                        cp ~/jobmail.env $DEST/.env
                        sudo bash $DEST/deploy/install.sh $USER_NAME
     Check JOBMAIL_DATA_DIR and OBSIDIAN_VAULT_PATH point at real paths for
     user $USER_NAME (the template assumes /home/pi).
  2. Set the clock:     sudo timedatectl set-timezone America/New_York
     (the poll timer is a wall-clock schedule: hourly 08:00-20:00, every day)
  3. Check credentials: sudo -u $USER_NAME $DEST/.venv/bin/python -m jobmail.preflight
  4. Test one run:      sudo -u $USER_NAME $DEST/.venv/bin/python -m jobmail.pipeline --dry-run -v
  5. Start polling:     sudo systemctl start jobmail-poll.timer
                        systemctl list-timers jobmail-poll
  6. Dashboard:         http://$IP:$PORT   (or via Tailscale)
  Logs:                 journalctl -u jobmail-poll -f
MSG
