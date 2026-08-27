# Kereby.dk apartment alert — status & setup (Telegram)


An unattended watcher that checks Kereby.dk's rental listings on a schedule and
sends a **Telegram message** the moment an apartment becomes **available** — a
brand-new listing, or one that flips from `Reserveret`/`Udlejet` to available. One
alert per event, no repeats, a clickable link straight to the unit, no server to
babysit.

The fetch and parse layer is **verified against the live site** (§1). What's
left is your Telegram bot and credentials (§2), and choosing where it runs (§3).

---

## 1. The live page, verified

The `Kereby recon` workflow (`.github/workflows/kereby-recon.yml` — run it from
the repository's Actions tab, no setup needed) fetched the real page and settled
every question that was open during the build. Raw results: issues #1 and #2.

- **URL:** `https://kereby.dk/bolig/`. Every other candidate 404s, including the
  original `/ledige-boliger/` guess. This is now `DEFAULT_URL`.
- **Server-rendered:** HTTP 200, ~195 KB, LiteSpeed, `text/html`. No headless
  browser needed, so `RENDER_JS` never has to be turned on. GitHub-hosted
  runners can reach the site, which makes Actions a usable host.
- **Platform:** Jorato (images served from `media.jorato.com`). Listing cards are
  `div.jorato-case-card`; taken ones also carry `jorato-case-card--unavailable`;
  the address sits in `span.jorato-case-card__location-text`. Taken cards are
  **not** wrapped in a link, which is why only available units have a detail URL.
- **Scale:** 15 listings on one page, 5 available at the time of the runs. No
  pagination seen.

### Three defects the live markup exposed

1. **Status was never detected — the worst possible failure.** The badge is a
   bare word (`Reserveret`, `Udlejet`) placed *before* the address, not
   `Status: Reserveret` after it as the original sample had. Read from the page's
   flat text, every badge is attributed to the previous listing, so nothing ever
   looks taken: the first run reported **15 of 15 available** and would have
   alerted on every already-rented flat. Fixed by `parse_listings_jorato()`,
   which works per card element so a badge can only apply to its own listing.
   A card marked `--unavailable` with no badge word reads as `reserved`, never
   available — a false "available" is a wasted alert.
2. **A link could point at the wrong flat.** `find_detail_url()` walked up out of
   a card into the surrounding grid and returned a neighbouring unit's href. It
   now stops at any ancestor containing more than one address.
3. **The area was silently dropped.** It is written `135 m<sup>2</sup>`, so text
   extraction split it into `"135 m"` and `"2"` and `sqm` came out `None` — which
   would also make a `MIN_SQM` filter reject every listing. `flatten_inline()`
   unwraps inline tags and calls `soup.smooth()` to rejoin the fragments
   (`unwrap()` alone leaves adjacent text nodes that still split), and `SQM_RE`
   accepts `m²`, `m2` and `kvm`.

`_selftest.py`'s `JORATO_SAMPLE` is rebuilt from this markup, so none of the
three can return unnoticed. The older text and card parsers remain as fallbacks
and are still covered by their own fixtures.

### If the page changes

A run that parses 0 listings fails loudly and leaves state untouched, so a
redesign surfaces as a failure rather than silence. To diagnose, run the recon
workflow again, or locally:

```bash
python kereby_watch.py --probe                 # which URL serves listings
python kereby_watch.py --inspect               # structure + the text lines seen
python kereby_watch.py --dump                  # parsed records + a status table
python kereby_watch.py --from-file dump.html   # re-parse offline while fixing
```

Keep `python kereby_watch.py --test` green, and if the record schema changes,
update the fixture with it.

Two things worth knowing before the next round of recon:

- **HTML tags do not survive being read back out of a GitHub issue body** — they
  are stripped. `--inspect` therefore reports an angle-bracket-free outline
  (`tag.class`, `href`, own text quoted) plus the exact text lines the parser
  sees, which is the information that actually matters.
- **Adopt an outside report's leads, never its data.** Two earlier passes through
  an AI assistant with web access got the URL and the server-rendered verdict
  right (both since confirmed — cheap to test, and self-correcting if wrong).
  Their listing data was wrong in exactly the way that mattered: it described the
  page in the shape of the old sample, `Status:` lines and all, which is the
  shape that caused defect 1 above. Get markup before trusting a snapshot.

## 2. What only you can do (bot & credentials)

**A. Create the Telegram bot (~2 min, free)**
1. In Telegram, open a chat with **@BotFather**.
2. Send `/newbot` and follow the prompts (a name, then a username ending in `bot`).
3. BotFather replies with a **bot token** like `123456789:AAH...` — that's
   `TELEGRAM_BOT_TOKEN`. Treat it like a password.

**B. Get your chat id — no terminal needed**
1. Open your new bot (BotFather gives a `t.me/<yourbot>` link), press **Start**,
   and send it any message. **A bot cannot message you until you talk to it
   first** — this step is not optional.
2. Add the token as the `TELEGRAM_BOT_TOKEN` secret (Settings → Secrets and
   variables → Actions → New repository secret).
3. Run the **Kereby setup** workflow from the Actions tab. The bot messages you
   your chat id; add that as the `TELEGRAM_CHAT_ID` secret. Run the workflow
   again and it sends a test message instead, confirming both secrets work.

Locally, `python kereby_watch.py --setup` does the same thing, and
`--get-chat-id` just prints the ids. (Telegram discards updates older than 24h,
so if you messaged the bot yesterday, send it another message first.)

**C. The repository** — done: this is it. It is deliberately **public**, because
GitHub only gives unmetered Actions minutes to public repositories, and that is
what pays for the 5-minute cadence. The history starts fresh here: the earlier
SMS-era commits contained the owner's phone number and were left behind in the
private repo this was extracted from.

**D. Add the secrets & (optional) filters in that repo**
Settings → Secrets and variables → Actions:
- **Secrets tab:**
  - `TELEGRAM_BOT_TOKEN` = the token from BotFather
  - `TELEGRAM_CHAT_ID` = your chat id from step B
- **Variables tab (all optional — leave unset to get everything):**
  - `KEREBY_URL` if §1 found a different listings URL
  - `MAX_PRICE` (e.g. `20000`), `MIN_ROOMS` (e.g. `3`), `MIN_SQM` (e.g. `70`),
    `AREAS` (comma list matched against the address, e.g. `København Ø,2100`)

**E. Verify end-to-end, then install on the Pi**

Full walkthrough: [`deploy/pi/README.md`](./deploy/pi/README.md). Locally first:
```bash
cp .env.example .env      # fill in TELEGRAM_BOT_TOKEN
set -a; source .env; set +a
python kereby_watch.py --get-chat-id # prints your chat id -> add to .env
set -a; source .env; set +a
python kereby_watch.py --test-msg    # sends one test Telegram message
python kereby_watch.py               # first real run: seeds state, no messages
```
Confirm the test message arrives and that a normal run prints
`First run: seeding state, no notification.` and writes `seen.json`. Then on the
Pi: `sudo ./deploy/pi/install.sh` — it self-tests, installs a systemd timer at
60s, and does one silent seeding run.

The watcher reports its own breakage over Telegram (one warning after 3 failed
runs, at most one per 6h, plus a recovery message), because on a Pi there is no
Actions run history to notice a red run.

**F. Hosting: settled**
Kereby is **first-come-first-served**, so cadence matters. This runs on GitHub
Actions at `*/5` (the fastest cron GitHub offers), which is free because Actions
minutes are unmetered on public repositories. A machine you control (see
`deploy/pi/`) gets a true 60s and works on any systemd Linux box, including a
cheap VPS. Run one or the other, never both: they keep separate state and would
each alert you for the same apartment.

## 3. What's in here

| File | Purpose |
|------|---------|
| `kereby_watch.py` | The whole watcher: fetch → parse → filter → diff → Telegram → state. |
| `_selftest.py` | Offline tests: both parsers, address handling, filters, message format and HTML escaping, diff over 4 runs, and the full run loop with a faked fetch/sender. |
| `deploy/pi/` | Pi deployment: `install.sh`, systemd service + timer, and the step-by-step `README.md`. |
| `.github/workflows/watch.yml` | Actions fallback (schedule disabled), optional Playwright, commits `seen.json` back. |
| `requirements.txt` | `requests`, `beautifulsoup4` (`playwright` only if needed). |
| `.env.example` | Local-testing env template. |
| `.gitignore` | Ignores `dump.html`, `.env`, `__pycache__`. |

### Design decisions (settled — don't re-litigate without a reason)
- **Not an "LLM agent."** A scheduled scraper + notifier. An LLM adds nothing to
  the core loop. Plain Python.
- **Telegram** for delivery: free, no account/credit/sender-id friction, reliable,
  and clickable links. (SMS via 46elks was the earlier plan, dropped for this
  reason — nothing in the fetch/parse/state layer changed with the swap.)
- **Raspberry Pi + systemd timer at 60s** for hosting, because Kereby is
  first-come-first-served. GitHub Actions (~15 min at best) is kept only as a
  manual fallback.
- **"Available" = no status line** on the listing. In the captured sample only 5
  of 14 units were actually available.
- **State in `seen.json`**, committed back by the workflow, so alerts fire once.
- **The address is the unit's identity key** — which is why the parser is careful
  never to let a neighbouring word bleed into it.

### How the fetch/parse path works
1. `requests` GET (3 attempts, exponential backoff), or Playwright per `RENDER_JS`.
2. **Line parser** (primary, proven against the captured sample): splits the page
   text on address lines containing a 4-digit postal code. Stops at the footer so
   page chrome can't leak into the last listing.
3. **Card parser** (fallback, used only if the line parser finds nothing): finds
   the smallest element around each `kr./md.` price that also contains an
   address, and parses each card separately — this is what handles an address
   split across tags.
4. Either way, each record gets a detail-page `url` where one can be found: the
   nearest wrapping `<a>` around the address, else the only link in its card.
   Relative hrefs resolve against the fetched URL. The message falls back to the
   listings page when no unit URL is found.

### Safety properties worth keeping
- 0 listings parsed → the run **fails** and leaves state untouched (a silent
  parse failure would mute the watcher forever while looking healthy).
- A failed send leaves that unit unrecorded, so the next run retries it; units
  already notified in the same batch are recorded immediately and never re-sent.
- More than `MAX_ALERTS_PER_RUN` (default 8) new units → one summary message
  instead of a burst.
- Telegram sends retry connection errors, timeouts, 429 (honouring
  `retry_after`) and 5xx, so a blip doesn't cost an alert for 15 minutes; a bad
  token, bad chat id or malformed HTML fails immediately with Telegram's own
  explanation instead of a stack trace. (A read timeout on a message that
  actually landed can duplicate it — the only ambiguous case, and a duplicate
  beats a missed apartment.)
- All page text is HTML-escaped before sending: `parse_mode=HTML` means an
  unescaped `&` or `<` in a listing title would otherwise make Telegram reject
  the whole message.
- The workflow's state commit runs even when the watcher fails, and retries the
  push with a rebase if a concurrent run got there first.
