# Running the watcher on a Raspberry Pi

Kereby is first-come-first-served, so this is the setup that matters: a Pi checks
**every 60 seconds** (vs. ~15 minutes on GitHub Actions), starts itself on boot,
and messages you on Telegram both when an apartment opens up and if the watcher
itself breaks.

Any Pi with network works — a Pi Zero 2 W is plenty. Assumes Raspberry Pi OS
(or any Debian/Ubuntu with systemd).

## 1. Get the code onto the Pi

SSH in, then:

```bash
sudo apt update && sudo apt install -y git python3-venv
git clone <your-private-repo-url> ~/kereby-watch
cd ~/kereby-watch
```

## 2. Create the Telegram bot (2 minutes, free)

On your phone or desktop Telegram:

1. Open a chat with **@BotFather**, send `/newbot`, and follow the prompts (a
   display name, then a username ending in `bot`).
2. BotFather replies with a **token** like `8123456789:AAH_xxxxxxxxxxxxxxxxx`.
   That's your `TELEGRAM_BOT_TOKEN` — treat it like a password.
3. Tap the `t.me/<yourbot>` link BotFather gives you, press **Start**, and send
   the bot any message ("hi" is fine). **This step is not optional** — Telegram
   forbids a bot from messaging you until you've messaged it first.

Then on the Pi:

```bash
cp .env.example .env
nano .env          # paste the token into TELEGRAM_BOT_TOKEN, save (Ctrl+O, Ctrl+X)

python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -a; source .env; set +a
.venv/bin/python kereby_watch.py --get-chat-id     # prints your chat id
```

Put that number in `.env` as `TELEGRAM_CHAT_ID`, then confirm delivery:

```bash
set -a; source .env; set +a
.venv/bin/python kereby_watch.py --test-msg        # a message should arrive
```

If `--get-chat-id` prints "No messages yet", you skipped step 3 above (or sent
the message more than 24h ago — send another and retry).

## 3. Check that Kereby's page is readable

This is the one thing that couldn't be verified when the watcher was built, and
it takes one command:

```bash
.venv/bin/python kereby_watch.py --dump
```

- **Ends with "parsed ~14 listings"** → done, go to step 4.
- **"parsed 0 listings"** → try `--probe`, which tests other likely URLs:
  ```bash
  .venv/bin/python kereby_watch.py --probe
  ```
  If it names a working URL, add it to `.env` as `KEREBY_URL=...` and re-run
  `--dump`.
- **Still 0** → the page is built by JavaScript, so a plain HTTP fetch only sees
  an empty shell. Install a headless browser and let the installer know:
  ```bash
  .venv/bin/pip install playwright
  .venv/bin/playwright install --with-deps chromium
  echo "RENDER_JS=true" >> .env
  .venv/bin/python kereby_watch.py --dump
  ```
  (On a Pi Zero this takes a few minutes and adds ~400 MB. A Pi 4/5 handles it
  comfortably. If it's too slow, the faster fix is finding Kereby's JSON API —
  see `HANDOFF.md` §1.)

If you'd rather not debug the page yourself, run:

```bash
.venv/bin/python kereby_watch.py --inspect
```

It prints the HTTP status and size, how many listings parsed, where a listing
sits in the page structure, and the markup of a single listing card — or, if
nothing parses, which JavaScript framework and data endpoints the page loads
instead. That output is small enough to paste from a phone and is exactly what
someone needs to finish the parser for you.

`../../RECON_PROMPT.md` is a ready-made brief for an AI assistant with internet
access. Treat whatever it returns with suspicion: ask for the markup `--inspect`
would have printed, and never accept a "live snapshot" that happens to match the
sample already in `_selftest.py` — that is a sign it read the repo instead of the
site.

## 4. Install the timer

```bash
sudo ./deploy/pi/install.sh
```

That creates a virtualenv, runs the offline self-test, installs a systemd
service + timer, keeps state in `/var/lib/kereby-watch/`, and does one seeding
run (which deliberately notifies nothing — otherwise you'd get a message for
every apartment already listed).

Options:

```bash
sudo INTERVAL=30s ./deploy/pi/install.sh    # check twice a minute
sudo RENDER_JS=true ./deploy/pi/install.sh  # also install headless Chromium
sudo STATE_DIR=/home/pi/kw-state ./deploy/pi/install.sh
```

From then on you get a Telegram message, within a minute, whenever an apartment
becomes available.

## Day-to-day

```bash
systemctl list-timers kereby-watch.timer   # when it next runs
journalctl -u kereby-watch -f              # live log
journalctl -u kereby-watch --since -1h     # recent history
systemctl start kereby-watch.service       # check right now
systemctl disable --now kereby-watch.timer # stop watching
```

A healthy run logs `Fetched 14 listings via requests/text.` and either
`Notified 0.` or the addresses it messaged you about.

To update after a `git pull`, re-run `sudo ./deploy/pi/install.sh` — it's
idempotent and keeps your state and `.env`.

## How it behaves when things go wrong

- **The site changes or goes down.** A run that parses 0 listings fails on
  purpose and leaves state untouched. After 3 consecutive failures you get one
  Telegram warning, then at most one more every 6 hours (`FAIL_ALERT_AFTER` and
  `FAIL_ALERT_HOURS` in `.env`), and a "working again" message when it recovers.
  A watcher that silently goes deaf is the real failure mode here.
- **Power cut / reboot.** The timer is enabled, so it starts 45s after boot.
  State lives in `/var/lib/kereby-watch/seen.json`, so you don't get re-alerted
  for apartments you already saw.
- **A slow check overlaps the next tick.** The run takes a lock; the later one
  logs "Another run is still going" and steps aside. No double messages.
- **Telegram hiccups.** Sends retry with backoff (and honour Telegram's
  `retry_after`). If a message still can't be delivered, that apartment stays
  unrecorded and is retried on the next tick a minute later, rather than being
  lost.
- **A burst of new listings.** More than `MAX_ALERTS_PER_RUN` (8) at once
  collapses into one summary message instead of flooding the chat.

## No systemd? (plain cron)

```cron
* * * * * cd /home/pi/kereby-watch && set -a && . ./.env && set +a && ./.venv/bin/python kereby_watch.py >> /var/log/kereby-watch.log 2>&1
```

The script's own lock keeps overlapping runs from double-notifying, so no
`flock` wrapper is needed. Set `STATE_FILE` in `.env` if you want state outside
the checkout.
