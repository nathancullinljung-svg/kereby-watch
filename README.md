# kereby-watch

Watches [Kereby.dk](https://kereby.dk) rental listings and sends a **Telegram
message** whenever an apartment becomes **available** — a new listing, or one that
flips from `Reserveret`/`Udlejet` to available. Alerts fire once per event, and each
message links straight to that unit's page. Runs on a Raspberry Pi at
**one check per minute** — Kereby is first-come-first-served, so latency is the
point. (A GitHub Actions fallback is included but can only manage ~15 min.)

**Setting it up? Read [`deploy/pi/README.md`](./deploy/pi/README.md)** — the
step-by-step Pi walkthrough (Telegram bot, one check against the live site,
install). [`HANDOFF.md`](./HANDOFF.md) has the design background.

## Quick start
```bash
pip install -r requirements.txt
python kereby_watch.py --test         # offline self-test, no network, no messages
python kereby_watch.py --dump         # confirm listings are fetchable (HANDOFF §1)
cp .env.example .env                  # add TELEGRAM_BOT_TOKEN
set -a; source .env; set +a
python kereby_watch.py --get-chat-id  # prints your chat id -> put in .env
python kereby_watch.py --test-msg     # test message
python kereby_watch.py                # first run seeds state, no messages
```

## Commands
| Command | What it does |
|---------|--------------|
| `python kereby_watch.py` | Normal run: fetch → filter → diff → notify → save state |
| `--dump` | Fetch and print raw HTML + parsed JSON to `dump.html`, no messages |
| `--probe` | Try candidate listing URLs, report which one parses listings |
| `--from-file dump.html` | Parse a saved page offline, no network, no messages |
| `--inspect ["Street 1"]` | Report where a listing sits in the DOM, print one card's markup, or — if nothing parses — what the page loads its data with (add `--chars N` for more markup) |
| `--test` | Offline self-test (parsers, filters, diff, full run loop) |
| `--get-chat-id` | Print the chat id(s) that have messaged your bot |
| `--test-msg` | Send one test Telegram message |

## Deploy

**GitHub Actions (in use):** `.github/workflows/kereby-watch.yml`, on a
`*/5` cron. Actions minutes are unmetered on public repositories, which is why
this repo is public — it buys a 5-minute cadence for free. Scheduled runs are
best-effort, so the real gap is usually 5–15 minutes.

Two secrets are required (Settings → Secrets and variables → Actions):
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Run the **Kereby setup** workflow
once to confirm them — it messages you your chat id, then sends a test message.

**Note on the repository being public:** the code holds no secrets (those live
in GitHub Secrets and are masked in logs), but run logs and `seen.json` are
world-readable. They contain Kereby's public listing data and the times
apartments came up. Nothing personal, but it is visible.

To go below GitHub's 5-minute cron floor, set the `LOOP_MINUTES` variable (e.g.
`50`) so a single run keeps checking, with `CHECK_SECONDS` (default 60) between
checks. That works, but it keeps a runner busy continuously — heavier use than
Actions is intended for, and GitHub may throttle it.

**A machine you control (60s, no caveats):** see
[`deploy/pi/README.md`](./deploy/pi/README.md) — written for a Raspberry Pi, but
`install.sh` works on any systemd Linux box, including a €4/month VPS. Run
either Actions *or* a machine, not both: they keep separate state and would each
alert you for the same apartment.

## Config (env vars)
| Var | Required | Meaning |
|-----|----------|---------|
| `TELEGRAM_BOT_TOKEN` | yes | bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | yes | your chat id (see `--get-chat-id`) |
| `KEREBY_URL` | no | override the listings URL (default `https://kereby.dk/bolig/`) |
| `STATE_FILE` | no | where to keep `seen.json` (default: next to the script) |
| `RENDER_JS` | no | `auto` (default) / `true` (always Playwright) / `false` (never) |
| `MAX_PRICE` | no | max kr./md. |
| `MIN_ROOMS` | no | minimum rooms |
| `MIN_SQM` | no | minimum m² |
| `AREAS` | no | comma list matched against the address |
| `MAX_ALERTS_PER_RUN` | no | default `8`; above that, one summary message instead |
| `NOTIFY_ON_FIRST_RUN` | no | `true` to also alert on the seeding run |
| `FAIL_ALERT_AFTER` | no | consecutive failed runs before a "watcher is broken" message (default `3`, `0` disables) |
| `FAIL_ALERT_HOURS` | no | minimum gap between those warnings (default `6`) |

## How it decides to text you
- On the listings page each unit shows `Status: Reserveret`, `Status: Udlejet`,
  or **no status line** — no status means available.
- A unit is newsworthy when it is available **now** and either was never seen
  before or was not available last run.
- `seen.json` stores the last known status per unit (keyed by its address), so
  an alert never repeats. The first run seeds it silently.
- If a run can't parse any listings it **fails loudly and leaves `seen.json`
  untouched**, so a site change shows up as a red Actions run instead of a
  watcher that has quietly gone deaf.
- If a send fails, that unit stays unrecorded and is retried on the next run.
- Repeated failures (site redesign, no network) get you one Telegram warning
  after 3 bad runs and a "working again" message on recovery — an unattended
  watcher going silently deaf is the failure mode that actually costs you an
  apartment.
- Overlapping runs are locked out, so a minute-by-minute cron can't double-send.
- More than `MAX_ALERTS_PER_RUN` (default 8) new units at once → one summary
  message instead of a burst.
