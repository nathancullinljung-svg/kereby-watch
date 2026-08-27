#!/usr/bin/env bash
# Install the Kereby watcher as a systemd timer on a Raspberry Pi (or any
# systemd Linux box). Idempotent: safe to re-run after a git pull.
#
#   sudo ./deploy/pi/install.sh                 # every 60s (default)
#   sudo INTERVAL=30s ./deploy/pi/install.sh    # every 30s
#   sudo RENDER_JS=true ./deploy/pi/install.sh  # also install headless Chromium
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_DIR="${STATE_DIR:-/var/lib/kereby-watch}"
INTERVAL="${INTERVAL:-60s}"
RUN_USER="${SUDO_USER:-$(id -un)}"

if [[ $EUID -ne 0 ]]; then
  echo "Run me with sudo: sudo $0" >&2; exit 1
fi
if ! command -v systemctl >/dev/null; then
  echo "No systemd here. Use the cron line in deploy/pi/README.md instead." >&2
  exit 1
fi
if [[ ! -f "$DIR/.env" ]]; then
  echo "Missing $DIR/.env -- copy .env.example to .env and put your" >&2
  echo "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in it first." >&2
  exit 1
fi

echo "==> Installing into $DIR (user: $RUN_USER, every $INTERVAL)"

echo "==> System packages"
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip

echo "==> Python venv"
sudo -u "$RUN_USER" python3 -m venv "$DIR/.venv" 2>/dev/null || true
sudo -u "$RUN_USER" "$DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$RUN_USER" "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

if [[ "${RENDER_JS:-}" == "true" ]]; then
  echo "==> Headless Chromium (RENDER_JS=true)"
  sudo -u "$RUN_USER" "$DIR/.venv/bin/pip" install -q playwright
  sudo -u "$RUN_USER" "$DIR/.venv/bin/playwright" install --with-deps chromium
  grep -q '^RENDER_JS=' "$DIR/.env" || echo "RENDER_JS=true" >> "$DIR/.env"
fi

echo "==> State directory $STATE_DIR"
install -d -o "$RUN_USER" -g "$RUN_USER" -m 755 "$STATE_DIR"
# Carry over state from a previous in-checkout run so it doesn't re-alert.
if [[ -f "$DIR/seen.json" && ! -f "$STATE_DIR/seen.json" ]]; then
  install -o "$RUN_USER" -g "$RUN_USER" -m 644 "$DIR/seen.json" "$STATE_DIR/seen.json"
  echo "    migrated existing seen.json"
fi
chmod 600 "$DIR/.env"; chown "$RUN_USER" "$DIR/.env"   # it holds the bot token

echo "==> Self-test (offline, sends nothing)"
sudo -u "$RUN_USER" "$DIR/.venv/bin/python" "$DIR/kereby_watch.py" --test >/dev/null
echo "    parser and diff logic OK"

echo "==> systemd units"
for f in kereby-watch.service kereby-watch.timer; do
  sed -e "s|__DIR__|$DIR|g" -e "s|__USER__|$RUN_USER|g" \
      -e "s|__STATE__|$STATE_DIR|g" -e "s|__INTERVAL__|$INTERVAL|g" \
      "$DIR/deploy/pi/$f" > "/etc/systemd/system/$f"
done
systemctl daemon-reload
systemctl enable --now kereby-watch.timer

echo
echo "==> Seeding state (this first run notifies nothing)"
systemctl start kereby-watch.service || true
sleep 2
systemctl --no-pager --lines=15 status kereby-watch.service || true

cat <<EOF

Done. The watcher now checks Kereby every $INTERVAL, starts itself on boot,
and keeps its state in $STATE_DIR.

  systemctl list-timers kereby-watch.timer   # when it next runs
  journalctl -u kereby-watch -f              # live log
  journalctl -u kereby-watch --since -1h     # recent history
  systemctl start kereby-watch.service       # check right now
  systemctl disable --now kereby-watch.timer # stop watching

If it ever breaks (a site redesign, no network), it messages you on Telegram
after a few failed checks in a row rather than going quietly deaf -- see
deploy/pi/README.md.
EOF
