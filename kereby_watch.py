#!/usr/bin/env python3
"""
Kereby.dk apartment watcher (Telegram edition).

Checks the Kereby listings page, detects apartments that are AVAILABLE
(either brand-new, or flipped from Reserveret/Udlejet to available),
and sends you a Telegram message with a link straight to the unit.

State is stored in seen.json so it only ever notifies you once per event.

Env vars (set as GitHub Secrets, or export locally):
  TELEGRAM_BOT_TOKEN             from @BotFather, e.g. 123456:AA...
  TELEGRAM_CHAT_ID               your chat id (see --get-chat-id below)
Optional filters:
  MAX_PRICE                      e.g. 20000  (kr./md.)
  MIN_ROOMS                      e.g. 3
  MIN_SQM                        e.g. 70
  AREAS                          comma list matched against the address,
                                 e.g. "København Ø,2100,Frederiksberg"
Optional behaviour:
  KEREBY_URL                     override the listings URL
  STATE_FILE                     where to keep seen.json (default: next to
                                 this script)
  FAIL_ALERT_AFTER               consecutive failed runs before a "watcher is
                                 broken" message (default 3; 0 disables)
  FAIL_ALERT_HOURS               don't repeat that warning more often than this
                                 (default 6)
  RENDER_JS                      "true" to render with Playwright instead of
                                 plain requests (needed if the page is
                                 JavaScript-rendered); "auto" (default) tries
                                 requests first and only falls back to
                                 Playwright if it is installed and requests
                                 found nothing
  MAX_ALERTS_PER_RUN             default 8; above this one summary message is
                                 sent instead of a burst
  NOTIFY_ON_FIRST_RUN            "true" to notify on the very first run
                                 (default false: first run just seeds state)

Usage:
  python kereby_watch.py               # normal run
  python kereby_watch.py --dump        # fetch + print raw HTML/parsed JSON, no message
  python kereby_watch.py --probe       # try candidate listing URLs, report which work
  python kereby_watch.py --from-file F # parse a saved HTML file offline, no message
  python kereby_watch.py --inspect     # report page structure + one card's markup
  python kereby_watch.py --test        # run offline self-test on bundled samples
  python kereby_watch.py --setup       # check the bot, report your chat id
  python kereby_watch.py --get-chat-id # print chat id(s) that have messaged the bot
  python kereby_watch.py --test-msg    # send one test Telegram message
"""

import os
import re
import sys
import json
import html
import time
import fcntl
import pathlib
import unicodedata
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ---- CONFIG -----------------------------------------------------------------
# Confirmed by a recon pass: /ledige-boliger/ (the original guess) 404s, and
# the site navigation points here. --probe still tests the alternatives.
DEFAULT_URL = "https://kereby.dk/bolig/"
KEREBY_URL = os.getenv("KEREBY_URL") or DEFAULT_URL

# Used by --probe when the default URL turns out to be wrong.
CANDIDATE_PATHS = [
    "/bolig/",
    "/boliger/",
    "/ledige-boliger/",
    "/ledige-lejemaal/",
    "/lejeboliger/",
    "/find-bolig/",
    "/udlejning/",
    "/",
]

# Overridable so a Pi/VPS install can keep state outside the checkout
# (e.g. STATE_FILE=/var/lib/kereby-watch/seen.json).
STATE_FILE = pathlib.Path(os.getenv("STATE_FILE")
                          or pathlib.Path(__file__).with_name("seen.json"))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "da,en;q=0.8"}

STATUS_MAP = {"reserveret": "reserved", "udlejet": "rented"}
# On the live page the status is a bare badge word, not "Status: Reserveret",
# and it sits BEFORE the address -- which is why it must be read per card
# (DOM-scoped) rather than from the page's flat text.
BARE_STATUS = STATUS_MAP
# Class names confirmed on kereby.dk/bolig/ (a Jorato-powered site).
CARD_CLASS = "jorato-case-card"
CARD_UNAVAILABLE = "jorato-case-card--unavailable"
CARD_ADDRESS_CLASS = "jorato-case-card__location-text"
# Call-to-action text that must not be mistaken for a listing's description.
CTA_WORDS = {"se bolig", "se boligen", "se mere", "læs mere", "detaljer",
             "vis bolig", "book fremvisning", "skriv dig op"}
FEATURE_WORDS = {"altan", "elevator", "delevenlig", "tagterrasse",
                 "penthouse", "have", "kælder", "parkering"}
ADDRESS_RE = re.compile(r",\s*\d{4}\s+\S")          # "..., 2100 København Ø"
PRICE_RE   = re.compile(r"([\d.]+)\s*kr\./md\.")
ROOMS_RE   = re.compile(r"^(\d+)\s+værelse")
SQM_RE     = re.compile(r"^(\d+)\s*(?:m²|m2|kvm)", re.I)
POSTCODE_LINE_RE = re.compile(r"^\d{4}\s+\S")   # "2100 København Ø" on its own
STREETISH_RE = re.compile(r"^[A-ZÆØÅ].*\d")      # "Stormgade 35, 4. th"
# Page chrome that follows the last listing; stop parsing when we hit it.
FOOTER_MARKERS = ("cookie", "cookies", "persondatapolitik", "privatlivspolitik",
                  "kontakt os", "følg os", "nyhedsbrev", "cvr", "©")

_SKIP_HREF = ("mailto:", "tel:", "javascript:", "#")


# ---- PARSING ----------------------------------------------------------------
def _blank_record(address: str) -> dict:
    return {"address": address, "status": "available", "title": None,
            "price": None, "rooms": None, "sqm": None, "features": [],
            "url": None}


def _fill_from_line(cur: dict, line: str) -> None:
    """Apply one detail line to the record under construction."""
    low = line.lower()
    if low.startswith("status:"):
        cur["status"] = STATUS_MAP.get(low.split(":", 1)[1].strip(), "available")
    elif PRICE_RE.search(line):
        cur["price"] = int(PRICE_RE.search(line).group(1).replace(".", ""))
    elif ROOMS_RE.search(line):
        cur["rooms"] = int(ROOMS_RE.search(line).group(1))
    elif SQM_RE.search(line):
        cur["sqm"] = int(SQM_RE.search(line).group(1))
    elif low in FEATURE_WORDS:
        cur["features"].append(line)
    elif low.startswith("obs:"):
        pass  # note line, ignore
    elif cur["title"] is None:
        cur["title"] = line  # first free-text line is the description


def parse_listings_text(text: str) -> list[dict]:
    """Parse the human-readable listings text into structured records.

    Records are delimited by address lines (a line containing a 4-digit
    Danish postal code). Everything between one address line and the next
    belongs to that record. Parsing stops at the page footer so footer
    chrome cannot leak into the last listing.
    """
    lines = [html.unescape(l.strip()) for l in text.splitlines()]
    lines = [l for l in lines if l]

    records, cur = [], None
    for line in lines:
        if ADDRESS_RE.search(line):
            if cur:
                records.append(cur)
            cur = _blank_record(line)
            continue
        if cur is None:
            continue  # skip header junk like "14 resultater"
        low = line.lower()
        if any(m in low for m in FOOTER_MARKERS):
            break  # into the page footer: this listing is complete
        _fill_from_line(cur, line)
    if cur:
        records.append(cur)

    for r in records:
        r["id"] = r["address"]  # address line is unique per unit
    return records


def _is_detail_line(line: str) -> bool:
    low = line.lower()
    return bool(low.startswith(("status:", "obs:"))
                or PRICE_RE.search(line) or ROOMS_RE.search(line)
                or SQM_RE.search(line) or low in FEATURE_WORDS)


def address_from_lines(lines: list[str]) -> tuple[str | None, set[str]]:
    """Recover one listing's address from a card's text lines.

    Returns (address, lines_that_formed_it). Handles both a whole address on
    one line and an address split across tags, where the postal code lands on
    its own line and the street part sits just above it. Only the street/city
    fragments are consumed, so no trailing word (e.g. "Status") can end up
    inside the address -- it is the state key, so it has to be stable.
    """
    for i, line in enumerate(lines):
        if ADDRESS_RE.search(line):
            return line, {line}
        if POSTCODE_LINE_RE.match(line):
            parts, j = [], i - 1
            while j >= 0 and len(parts) < 3:
                prev = lines[j]
                if (_is_detail_line(prev) or len(prev) > 70
                        or not (prev.endswith(",") or STREETISH_RE.match(prev))):
                    break
                parts.insert(0, prev)
                j -= 1
                if not prev.endswith(","):
                    break     # a street line without a trailing comma ends it
            if parts:
                addr = " ".join(p.rstrip(",").strip() + "," for p in parts)
                return f"{addr} {line}".strip(), set(parts) | {line}
    return None, set()


def _card_status(card, lines: list[str]) -> str:
    """Read one card's status. Conservative: only 'available' when sure."""
    for line in lines:
        low = line.lower().strip().rstrip(":")
        if low.startswith("status:"):
            return STATUS_MAP.get(low.split(":", 1)[1].strip(), "available")
        if low in BARE_STATUS:
            return BARE_STATUS[low]
    if CARD_UNAVAILABLE in (card.get("class") or []):
        # Marked taken, but no badge word to say which kind. Not available is
        # the only safe reading -- a false "available" is a wasted alert.
        return "reserved"
    return "available"


def parse_listings_jorato(soup, base_url: str = KEREBY_URL) -> list[dict]:
    """Parse the real kereby.dk/bolig/ markup, one record per case card.

    Scoping to the card element is what makes the status correct: the badge
    ("Reserveret"/"Udlejet") precedes the address in the page's text flow, so
    any parser working off the flat text attributes it to the wrong listing.
    """
    records, seen = [], set()
    for card in soup.find_all(class_=CARD_CLASS):
        lines = _text_lines(card)
        if not any(PRICE_RE.search(l) for l in lines):
            continue                      # not a listing card
        node = card.find(class_=CARD_ADDRESS_CLASS)
        address, used = (" ".join(node.get_text(" ").split()), set()) if node \
            else address_from_lines(lines)
        if not address or address in seen:
            continue
        seen.add(address)

        cur = _blank_record(address)
        cur["status"] = _card_status(card, lines)
        for line in lines:
            low = line.lower().strip().rstrip(":")
            if (line in used or line in address or low in BARE_STATUS
                    or low.startswith("status:") or low in CTA_WORDS
                    or ADDRESS_RE.search(line)):
                continue
            _fill_from_line(cur, line)
        link = card.find("a", href=True)
        if link and _plausible_href(link["href"]):
            cur["url"] = urljoin(base_url, link["href"])
        cur["id"] = address
        records.append(cur)
    return records


def _cards(soup) -> list:
    """Smallest elements that look like one listing card each.

    A card is the smallest ancestor of a "kr./md." price whose own text also
    yields an address. Used only when the line-based parser finds nothing --
    e.g. when the address is split across tags so it never lands on its own
    text line.
    """
    seen, cards = set(), []
    for node in soup.find_all(string=PRICE_RE):
        el = node.parent
        for _ in range(10):
            if el is None or getattr(el, "name", None) is None:
                break
            addr, _used = address_from_lines(_text_lines(el))
            if addr:
                if id(el) not in seen:
                    seen.add(id(el))
                    cards.append(el)
                break
            el = el.parent
    return cards


def flatten_inline(soup):
    """Merge inline tags into their parent's text.

    The live page writes the area as `135 m<sup>2</sup>`, which text extraction
    would otherwise split into "135 m" and "2", losing the square metres.
    """
    for tag in soup.find_all(["sup", "sub"]):
        tag.unwrap()
    # unwrap() leaves the fragments as separate text nodes, which still split
    # on extraction; smooth() consolidates them into one.
    soup.smooth()
    return soup


def _text_lines(el) -> list[str]:
    return [l.strip() for l in el.get_text("\n").splitlines() if l.strip()]


def parse_listings_html(soup) -> list[dict]:
    """Fallback parser: one record per detected card element."""
    records, seen = [], set()
    for card in _cards(soup):
        lines = _text_lines(card)
        address, used = address_from_lines(lines)
        if not address or address in seen:
            continue      # nested cards can yield the same address twice
        seen.add(address)
        cur = _blank_record(address)
        for line in lines:
            if line in used or ADDRESS_RE.search(line):
                continue
            _fill_from_line(cur, line)
        cur["id"] = address
        records.append(cur)
    return records


def _plausible_href(href: str) -> bool:
    h = href.strip().lower()
    return bool(h) and not h.startswith(_SKIP_HREF)


def find_detail_url(soup, address: str, base_url: str) -> str | None:
    """Best-effort detail-page URL for one listing.

    Finds the address text in the DOM and walks up to the nearest wrapping
    link; failing that, uses the nearest ancestor block that contains
    exactly one plausible link.
    """
    street = address.split(",")[0].strip()
    if not street:
        return None
    node = soup.find(string=lambda s: s and street in " ".join(s.split()))
    if node is None:
        return None

    el = node.parent
    for _ in range(8):
        if el is None or getattr(el, "name", None) is None:
            break
        if el.name == "a" and _plausible_href(el.get("href", "")):
            return urljoin(base_url, el["href"])
        el = el.parent

    el = node.parent
    for _ in range(8):
        if el is None or getattr(el, "name", None) is None:
            break
        # Never walk up past this listing: an ancestor holding two addresses is
        # a grid of cards, and its "only link" belongs to a different unit.
        if len(ADDRESS_RE.findall(" ".join(el.get_text(" ").split()))) > 1:
            break
        hrefs = {a["href"] for a in el.find_all("a", href=True)
                 if _plausible_href(a["href"])}
        if len(hrefs) == 1:
            return urljoin(base_url, hrefs.pop())
        el = el.parent
    return None


# Kereby's detail pages are /bolig/<slugified-address>/. Danish letters are
# transliterated, and which scheme a site uses varies (ø -> o or oe, å -> a or
# aa), so rather than guess we build the candidates and ask the server which
# one exists. A derived link is only ever used after it answers 200 -- a dead
# link in an alert is worse than linking to the listings page.
TRANSLITERATIONS = (
    {"ø": "o", "å": "a", "æ": "ae"},
    {"ø": "oe", "å": "aa", "æ": "ae"},     # WordPress da_DK style
)
_url_cache: dict[str, str | None] = {}


def slugify_address(address: str, table: dict[str, str]) -> str:
    s = address.lower().replace("'", "").replace("\u2019", "")
    for danish, ascii_ in table.items():
        s = s.replace(danish, ascii_)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def detail_url_candidates(address: str, base_url: str = KEREBY_URL) -> list[str]:
    out = []
    for table in TRANSLITERATIONS:
        slug = slugify_address(address, table)
        if not slug:
            continue
        url = urljoin(base_url, f"/bolig/{slug}/")
        if url not in out:
            out.append(url)
    return out


def url_exists(url: str) -> bool:
    try:
        r = requests.head(url, headers=HEADERS, timeout=15, allow_redirects=True)
        if r.status_code == 405:      # some servers reject HEAD outright
            r = requests.get(url, headers=HEADERS, timeout=15, stream=True)
        return r.status_code == 200
    except requests.RequestException:
        return False


def resolve_detail_url(record: dict, base_url: str = KEREBY_URL) -> str | None:
    """Fill in a listing's detail URL when the page didn't link to it."""
    if record.get("url"):
        return record["url"]
    address = record["address"]
    if address in _url_cache:
        record["url"] = _url_cache[address]
        return record["url"]
    found = next((u for u in detail_url_candidates(address, base_url)
                  if url_exists(u)), None)
    _url_cache[address] = found
    record["url"] = found
    if found:
        print(f"  resolved detail URL: {found}")
    return found


def parse_page(page_html: str, base_url: str = KEREBY_URL) -> tuple[list[dict], str]:
    """Parse a listings page. Returns (records, strategy_used)."""
    soup = flatten_inline(BeautifulSoup(page_html, "html.parser"))
    records = parse_listings_jorato(soup, base_url)
    strategy = "jorato"
    if not records:
        records = parse_listings_text(soup.get_text(separator="\n"))
        strategy = "text"
    if not records:
        records = parse_listings_html(soup)
        strategy = "cards"
    for r in records:
        if not r.get("url"):
            r["url"] = find_detail_url(soup, r["address"], base_url)
    return records, strategy


# ---- FETCHING ---------------------------------------------------------------
def fetch_html(url: str = KEREBY_URL, attempts: int = 3) -> str:
    """GET the page with retries on transient network/5xx failures."""
    last = None
    for i in range(attempts):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code >= 500:
                raise requests.HTTPError(f"{resp.status_code} from {url}")
            resp.raise_for_status()
            resp.encoding = resp.encoding or "utf-8"
            return resp.text
        except (requests.RequestException, requests.HTTPError) as e:
            last = e
            if i < attempts - 1:
                time.sleep(2 ** i)
    raise SystemExit(f"ERROR: could not fetch {url}: {last}")


def render_html(url: str = KEREBY_URL) -> str:
    """Render the page in headless Chromium (for JS-rendered listings)."""
    from playwright.sync_api import sync_playwright  # optional dependency
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(user_agent=UA, locale="da-DK")
            page.goto(url, wait_until="networkidle", timeout=60_000)
            return page.content()
        finally:
            browser.close()


def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


def fetch_listings(url: str = KEREBY_URL) -> tuple[str, list[dict], str]:
    """Fetch and parse the listings page.

    Returns (raw_html, records, how). RENDER_JS controls the strategy:
      "true"  -> Playwright only
      "false" -> requests only
      "auto"  -> requests, falling back to Playwright if it parsed nothing
                 and Playwright is installed (the default)
    """
    mode = os.getenv("RENDER_JS", "auto").strip().lower()

    if mode in ("true", "1", "yes"):
        raw = render_html(url)
        records, strategy = parse_page(raw, url)
        return raw, records, f"playwright/{strategy}"

    raw = fetch_html(url)
    records, strategy = parse_page(raw, url)
    if records or mode in ("false", "0", "no") or not _playwright_available():
        return raw, records, f"requests/{strategy}"

    print("  requests found 0 listings; retrying with Playwright...")
    raw = render_html(url)
    records, strategy = parse_page(raw, url)
    return raw, records, f"playwright/{strategy}"


def probe() -> None:
    """Try candidate listing URLs and report which ones parse listings."""
    base = f"{urlparse(KEREBY_URL).scheme}://{urlparse(KEREBY_URL).netloc}"
    urls = [KEREBY_URL] + [urljoin(base, p) for p in CANDIDATE_PATHS]
    seen, results = set(), []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        try:
            raw = fetch_html(url, attempts=1)
        except SystemExit as e:
            results.append((url, f"unreachable ({e})", 0))
            continue
        records, strategy = parse_page(raw, url)
        avail = sum(1 for r in records if r["status"] == "available")
        results.append((url, f"{len(raw)} bytes, parser={strategy}, "
                             f"{avail} available", len(records)))
    print("Candidate listing URLs:\n")
    for url, note, n in sorted(results, key=lambda x: -x[2]):
        print(f"  {n:3d} listings  {url}\n               {note}")
    best = max(results, key=lambda x: x[2])
    print()
    if best[2]:
        print(f"Use KEREBY_URL={best[0]}  ({best[2]} listings parsed)")
    else:
        print("No candidate URL yielded listings. Either the page is "
              "JavaScript-rendered (try RENDER_JS=true, or find the JSON API "
              "in DevTools -> Network -> Fetch/XHR) or the listings live at a "
              "URL not in CANDIDATE_PATHS.")


# ---- INSPECTION -------------------------------------------------------------
# Turns "paste me the page HTML" into one command with a small, phone-sized
# output: where a listing lives in the DOM, the markup of one card, and -- if
# there are no listings at all -- what the page loads its data with instead.
JS_MARKERS = ("__NEXT_DATA__", "window.__NUXT__", "__remixContext",
              "window.__INITIAL_STATE__", "/wp-json/", "graphql", "/api/")


def _fetch_verbose(url: str) -> str:
    print(f"GET {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        # A readable one-liner: this output gets pasted into reports.
        raise SystemExit(f"  FAILED: {type(e).__name__}: {e}")
    print(f"  status : {resp.status_code}")
    print(f"  bytes  : {len(resp.content)}")
    print(f"  final  : {resp.url}")
    print(f"  server : {resp.headers.get('server', '?')} / "
          f"{resp.headers.get('content-type', '?')}")
    resp.raise_for_status()
    return resp.text


def inspect_page(page_html: str, needle: str | None = None,
                 base_url: str = KEREBY_URL, chars: int = 4000) -> None:
    soup = flatten_inline(BeautifulSoup(page_html, "html.parser"))
    records, strategy = parse_page(page_html, base_url)
    print(f"\nParsed {len(records)} listings via the {strategy} parser "
          f"({sum(1 for r in records if r['status'] == 'available')} available, "
          f"{sum(1 for r in records if r['url'])} with a detail URL).")

    if not needle:
        needle = records[0]["address"].split(",")[0] if records else None
    if not needle:
        # No listings at all: say what the page *does* contain, which is what
        # decides between "wrong URL" and "JavaScript-rendered".
        print("\nNo listing found to inspect. Page contents instead:")
        scripts = soup.find_all("script")
        print(f"  <script> tags: {len(scripts)} "
              f"({sum(1 for t in scripts if t.get('src'))} external)")
        hits = [m for m in JS_MARKERS if m in page_html]
        print(f"  data markers : {', '.join(hits) if hits else 'none found'}")
        for tag in soup.find_all("script", type="application/json")[:3]:
            print(f"  inline JSON  : id={tag.get('id')} "
                  f"{len(tag.get_text())} chars, starts "
                  f"{tag.get_text()[:120]!r}")
        for tag in soup.find_all("script", src=True)[:8]:
            print(f"  script src   : {tag['src']}")
        body = " ".join(soup.get_text(" ").split())
        print(f"\n  visible text ({len(body)} chars), first 600:\n  {body[:600]}")
        print("\nIf the markers or script names above look like a JS app, the "
              "listings are rendered client-side: find the request that returns "
              "them (RENDER_JS=true is the fallback). If the page looks like a "
              "different section of the site, try --probe.")
        return

    node = soup.find(string=lambda t: t and needle in " ".join(t.split()))
    if node is None:
        print(f"\n{needle!r} does not appear in this page's text.")
        return

    print(f"\nAncestors of {needle!r} (innermost first):")
    el, chain = node.parent, []
    for depth in range(8):
        if el is None or getattr(el, "name", None) is None:
            break
        chain.append(el)
        print(f"  {depth}: {_label(el)}"
              + (f" id={el.get('id')}" if el.get("id") else "")
              + (f" href={el.get('href')}" if el.get("href") else ""))
        el = el.parent

    # The smallest ancestor holding a whole listing: address *and* price.
    card = None
    for el in chain:
        lines = _text_lines(el)
        if address_from_lines(lines)[0] and any(PRICE_RE.search(l) for l in lines):
            card = el
            break
    if card is None:
        print("\nNo ancestor contains both an address and a price -- the card "
              "markup may sit above the 8 levels shown.")
        return
    print(f"\nSmallest element containing one full listing: {_label(card)}")
    print("\n  text lines the parser sees:")
    for line in _text_lines(card):
        print(f"    {line[:120]!r}")
    print("\n  links inside this card:")
    links = [a for a in card.find_all("a", href=True)
             if _plausible_href(a["href"])]
    for a in links[:5]:
        print(f"    {_label(a)} href={a['href']}")
    if not links:
        print("    (none -- this card is not clickable)")
    print(f"\n  structure (tag.class, own text in quotes):")
    for line in _outline(card):
        print("    " + line)


def _label(el) -> str:
    return el.name + "".join("." + c for c in (el.get("class") or []))


_OUTLINE_SKIP = {"img", "picture", "source", "svg", "script", "style",
                 "noscript", "path", "use"}


def _outline(el, depth: int = 0, max_depth: int = 6,
             max_children: int = 14) -> list[str]:
    """A compact tag/class tree. No angle brackets: this output travels
    through GitHub issue bodies, which strip HTML tags."""
    own = " ".join(" ".join(t.split()) for t in el.find_all(string=True,
                                                            recursive=False)
                   if t.strip())
    line = "  " * depth + _label(el)
    if el.get("href"):
        line += f" href={el['href']}"
    if own:
        line += f"  {own[:70]!r}"
    out = [line]
    if depth >= max_depth:
        return out
    kids = [k for k in el.find_all(recursive=False)
            if k.name not in _OUTLINE_SKIP]
    for kid in kids[:max_children]:
        out += _outline(kid, depth + 1, max_depth, max_children)
    if len(kids) > max_children:
        out.append("  " * (depth + 1) + f"... +{len(kids) - max_children} more")
    return out


# ---- FILTERING --------------------------------------------------------------
def passes_filters(r: dict) -> bool:
    mx = os.getenv("MAX_PRICE")
    if mx and (r["price"] is None or r["price"] > int(mx)):
        return False
    mr = os.getenv("MIN_ROOMS")
    if mr and (r["rooms"] is None or r["rooms"] < int(mr)):
        return False
    ms = os.getenv("MIN_SQM")
    if ms and (r["sqm"] is None or r["sqm"] < int(ms)):
        return False
    areas = os.getenv("AREAS")
    if areas:
        wanted = [a.strip().lower() for a in areas.split(",") if a.strip()]
        if not any(a in r["address"].lower() for a in wanted):
            return False
    return True


# ---- STATE / DIFF -----------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def find_new_available(current: list[dict], state: dict) -> list[dict]:
    """A listing is newsworthy if it's available now AND
    (we've never seen it, or last time it was NOT available)."""
    hits = []
    for r in current:
        was = state.get(r["id"])
        if r["status"] == "available" and was != "available":
            hits.append(r)
    return hits


# ---- HEALTH (unattended runs) ------------------------------------------------
# On a Pi there is no Actions run history to notice a red run, so the watcher
# reports its own breakage over the same channel it uses for apartments -- once
# per FAIL_ALERT_HOURS, not once per minute.
def _health_file() -> pathlib.Path:
    return STATE_FILE.with_name("health.json")


def load_health() -> dict:
    f = _health_file()
    if f.exists():
        try:
            return json.loads(f.read_text())
        except json.JSONDecodeError:
            pass
    return {"failures": 0, "last_alert": 0, "alerted": False}


def save_health(h: dict) -> None:
    _health_file().write_text(json.dumps(h, indent=2))


def record_failure(reason: str, now: float | None = None) -> None:
    """Count a failed run and, past the threshold, say so over Telegram."""
    now = time.time() if now is None else now
    h = load_health()
    h["failures"] = h.get("failures", 0) + 1
    h["last_error"] = reason[:500]
    after = int(os.getenv("FAIL_ALERT_AFTER", "3"))
    cooldown = float(os.getenv("FAIL_ALERT_HOURS", "6")) * 3600
    due = (after and h["failures"] >= after
           and now - h.get("last_alert", 0) >= cooldown)
    if due:
        try:
            send_telegram(
                f'⚠️ <b>Kereby watcher is not working</b>\n'
                f'{h["failures"]} runs in a row have failed.\n'
                f'<code>{html.escape(reason[:300], quote=True)}</code>\n'
                f'Apartment alerts are paused until this is fixed.')
            h["last_alert"], h["alerted"] = now, True
        except SystemExit as e:
            # Can't even send -- the journal is the only place left to say it.
            print(f"  (could not send failure alert: {e})")
    save_health(h)


def record_success(now: float | None = None) -> None:
    """Clear the failure streak, and say so if we had cried wolf."""
    now = time.time() if now is None else now
    h = load_health()
    if h.get("alerted"):
        try:
            send_telegram("✅ <b>Kereby watcher is working again</b>\n"
                          "Apartment alerts have resumed.")
        except SystemExit as e:
            print(f"  (could not send recovery alert: {e})")
    if h.get("failures") or h.get("alerted"):
        save_health({"failures": 0, "last_alert": h.get("last_alert", 0),
                     "alerted": False, "last_ok": now})


# ---- NOTIFY (Telegram) ------------------------------------------------------
API = "https://api.telegram.org"


def _facts(r: dict) -> str:
    facts = []
    if r["rooms"]: facts.append(f'{r["rooms"]} vær')
    if r["sqm"]:   facts.append(f'{r["sqm"]} m²')
    if r["price"]: facts.append(f'{r["price"]:,} kr/md'.replace(",", "."))
    return " · ".join(facts)


def _esc(value) -> str:
    """Escape for Telegram's HTML parse mode (also safe inside href="...")."""
    return html.escape(str(value), quote=True)


def format_message(r: dict) -> str:
    """HTML-formatted Telegram message with a clickable link to the unit."""
    lines = [f'🏠 <b>{_esc(r["title"] or r["address"])}</b>', _esc(r["address"])]
    facts = _facts(r)
    if facts:
        lines.append(_esc(facts))
    lines.append(f'<a href="{_esc(r.get("url") or KEREBY_URL)}">'
                 f'Se boligen på Kereby</a>')
    return "\n".join(lines)


def format_summary_message(hits: list[dict]) -> str:
    """One message for a burst, so a bulk update can't spam the chat."""
    lines = [f"🏠 <b>{len(hits)} nye ledige boliger hos Kereby</b>"]
    for r in hits[:6]:
        facts = _facts(r)
        lines.append(f'• <a href="{_esc(r.get("url") or KEREBY_URL)}">'
                     f'{_esc(r["address"])}</a>'
                     + (f" — {_esc(facts)}" if facts else ""))
    if len(hits) > 6:
        lines.append(f'…+{len(hits) - 6} flere — '
                     f'<a href="{_esc(KEREBY_URL)}">se alle</a>')
    return "\n".join(lines)


def _require(*names: str) -> None:
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        raise SystemExit("ERROR: missing env var(s): " + ", ".join(missing)
                         + " -- see README/HANDOFF for Telegram bot setup.")


def _telegram(method: str, *, attempts: int = 3, **kwargs) -> dict:
    """Call one Telegram API method, retrying transient failures.

    Retries connection errors, timeouts, 429 and 5xx -- a blip should not cost
    an alert for the next 15 minutes. Anything else (bad token, bad chat id,
    malformed HTML) is a real error and is reported with Telegram's own
    explanation, which is far more useful than a stack trace.
    """
    _require("TELEGRAM_BOT_TOKEN")
    url = f"{API}/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{method}"
    for i in range(attempts):
        last = None
        try:
            r = requests.post(url, timeout=30, **kwargs)
            if r.status_code == 429 or r.status_code >= 500:
                wait = int(r.json().get("parameters", {})
                           .get("retry_after", 2 ** i)) if r.status_code == 429 \
                    else 2 ** i
                last = f"{r.status_code}: {r.text.strip()}"
            else:
                if not r.ok:
                    raise SystemExit(f"ERROR: Telegram rejected {method} "
                                     f"({r.status_code}): {r.text.strip()}")
                return r.json()
        except requests.RequestException as e:
            last, wait = f"{type(e).__name__}: {e}", 2 ** i
        if i < attempts - 1:
            print(f"  Telegram {method} failed ({last}); retrying in {wait}s")
            time.sleep(wait)
    raise SystemExit(f"ERROR: Telegram {method} failed after {attempts} "
                     f"attempts -- {last}")


def send_telegram(message: str, chat_id: str | None = None) -> None:
    if chat_id is None:
        _require("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
        chat_id = os.environ["TELEGRAM_CHAT_ID"]
    data = _telegram("sendMessage",
                     json={"chat_id": chat_id,
                           "text": message,
                           "parse_mode": "HTML",
                           "disable_web_page_preview": False})
    print("  Telegram sent:", data.get("result", {}).get("message_id", "ok"))


def discover_chats() -> dict[str, str]:
    """Chat ids that have messaged the bot."""
    updates = _telegram("getUpdates").get("result", [])
    found = {}
    for u in updates:
        chat = (u.get("message") or u.get("edited_message")
                or u.get("channel_post") or {}).get("chat", {})
        if chat.get("id"):
            found[str(chat["id"])] = (chat.get("username")
                                      or chat.get("first_name")
                                      or chat.get("title") or chat.get("type"))
    return found


def get_chat_id() -> None:
    """Print chat ids of anyone who has messaged the bot.

    Run this AFTER opening the bot in Telegram and pressing Start / sending it
    a message -- a bot cannot message you until you have talked to it first.
    """
    chats = discover_chats()
    if not chats:
        print("No messages yet. Open your bot in Telegram, press Start / send "
              "it any message, then run this again. (Telegram also drops "
              "updates older than 24h.)")
        return
    for cid, name in chats.items():
        print(f"  chat_id: {cid}   ({name})")
    print("\nUse the chat_id above as TELEGRAM_CHAT_ID.")


def setup_check() -> str:
    """Confirm the bot works, and deliver the chat id over Telegram itself.

    Written for someone with no terminal: the value they need to copy arrives
    as a message on their phone rather than as console output. Returns the
    state reached -- "verified", "id_sent" or "no_chats" -- so a caller can
    tell "you still need to message your bot" (normal, expected) apart from a
    real failure.
    """
    _require("TELEGRAM_BOT_TOKEN")

    # Naming the bot matters: the usual reason no chat is found is that the
    # message went to a different bot than the token belongs to.
    me = _telegram("getMe").get("result", {})
    username = me.get("username")
    print(f"Token is valid. It belongs to: @{username} "
          f"({me.get('first_name') or '?'})")
    if username:
        print(f"That is the bot you must message: https://t.me/{username}")

    configured = os.getenv("TELEGRAM_CHAT_ID")
    if configured:
        send_telegram("✅ <b>Kereby watcher is connected</b>\n"
                      "This is a test message. You'll get one like this the "
                      "moment an apartment becomes available.")
        print(f"Test message sent to the configured chat ({configured}).")
        print("Setup complete: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID both work.")
        return "verified"

    # A webhook silently swallows updates, so getUpdates would stay empty
    # however many times you message the bot.
    hook = _telegram("getWebhookInfo").get("result", {})
    if hook.get("url"):
        print(f"\nWARNING: this bot has a webhook set ({hook['url']}). While one "
              f"is set, getUpdates returns nothing. Delete it with:\n"
              f"  curl -X POST "
              f"https://api.telegram.org/bot<TOKEN>/deleteWebhook")

    chats = discover_chats()
    if not chats:
        print("\nNot connected yet: this bot has never received a message.")
        print("A Telegram bot cannot message you until you message it first.")
        print("\nDo this, in Telegram:")
        if username:
            print(f"  1. Open https://t.me/{username}  (this exact bot)")
        else:
            print("  1. Open your bot's chat")
        print("  2. Press the START button at the bottom of the chat")
        print("  3. Type any message, e.g. hi, and send it")
        print("  4. Run this again")
        print("\nIf you did message a bot already, check it was @"
              f"{username or '<your bot>'} and not a different one -- that is "
              "the most common cause. Telegram also discards updates older "
              "than 24h, so if it was yesterday, send another message.")
        return "no_chats"
    for cid, name in chats.items():
        send_telegram(
            "👋 <b>Kereby watcher: your bot works</b>\n"
            f"Your chat id is <code>{html.escape(cid)}</code>\n"
            "Add it as the GitHub secret <b>TELEGRAM_CHAT_ID</b> and the "
            "watcher is ready. (Tap the id to copy it.)", chat_id=cid)
        print(f"  chat_id: {cid}   ({name})  -- id sent to that chat")
    print("\nAdd the chat id above as the TELEGRAM_CHAT_ID secret.")
    return "id_sent"


# ---- MAIN -------------------------------------------------------------------
def run():
    _, fetched, how = fetch_listings()
    print(f"Fetched {len(fetched)} listings via {how}.")
    if not fetched:
        # Never silently succeed: a broken fetch must fail the run loudly,
        # otherwise the watcher goes quiet and looks healthy forever.
        raise SystemExit(
            "ERROR: 0 listings parsed. The page layout, the URL, or the "
            "rendering mode changed. Run `--probe` and `--dump` to diagnose. "
            "State left untouched.")

    state = load_state()
    first_run = not state
    current = [r for r in fetched if passes_filters(r)]
    print(f"{len(current)} listings pass the filters.")

    hits = find_new_available(current, state)
    if first_run and os.getenv("NOTIFY_ON_FIRST_RUN", "false").lower() != "true":
        print("First run: seeding state, no notification.")
        hits = []

    new_state = dict(state)
    notified: set[str] = set()
    try:
        cap = int(os.getenv("MAX_ALERTS_PER_RUN", "8"))
        if len(hits) > cap:
            print(f"{len(hits)} hits exceeds MAX_ALERTS_PER_RUN={cap}: "
                  f"sending one summary message.")
            for r in hits:
                resolve_detail_url(r)
            send_telegram(format_summary_message(hits))
            notified = {r["id"] for r in hits}
        else:
            for r in hits:
                print("NEW AVAILABLE:", r["address"])
                resolve_detail_url(r)
                send_telegram(format_message(r))
                # Record each send immediately so a later failure in this
                # loop can't cause a duplicate message on the next run.
                notified.add(r["id"])
                new_state[r["id"]] = r["status"]
                save_state(new_state)
    finally:
        # Record everything we currently see -- except alerts we owed but
        # never managed to send, which stay unrecorded so the next run
        # retries them instead of swallowing them.
        pending = {r["id"] for r in hits} - notified
        for r in current:
            if r["id"] not in pending:
                new_state[r["id"]] = r["status"]
        save_state(new_state)
        if pending:
            print(f"{len(pending)} alert(s) undelivered; will retry next run.")
    print(f"Notified {len(hits)}. State has {len(new_state)} listings.")


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--setup" in argv:
        # Exit 3 means "still waiting for you to message the bot" -- expected
        # mid-setup, and worth distinguishing from a real failure.
        sys.exit(3 if setup_check() == "no_chats" else 0)
    elif "--get-chat-id" in argv:
        get_chat_id()
    elif "--test-msg" in argv:
        send_telegram("✅ Kereby watcher: test message. Alerts are wired up.")
    elif "--probe" in argv:
        probe()
    elif "--inspect" in argv:
        i = argv.index("--inspect")
        # Don't mistake another flag's value (a file path, a number) for the
        # address to look for.
        taken = {argv[argv.index(f) + 1] for f in ("--from-file", "--chars")
                 if f in argv and argv.index(f) + 1 < len(argv)}
        rest = [a for a in argv[i + 1:]
                if not a.startswith("--") and a not in taken]
        chars = int(argv[argv.index("--chars") + 1]) if "--chars" in argv else 4000
        if "--from-file" in argv:
            path = pathlib.Path(argv[argv.index("--from-file") + 1])
            inspect_page(path.read_text(), rest[0] if rest else None,
                         KEREBY_URL, chars)
        else:
            inspect_page(_fetch_verbose(KEREBY_URL),
                         rest[0] if rest else None, KEREBY_URL, chars)
    elif "--from-file" in argv:
        path = pathlib.Path(argv[argv.index("--from-file") + 1])
        records, strategy = parse_page(path.read_text(), KEREBY_URL)
        print(json.dumps(records, ensure_ascii=False, indent=2))
        avail = sum(1 for r in records if r["status"] == "available")
        with_url = sum(1 for r in records if r["url"])
        print(f"\n{path}: parsed {len(records)} listings ({avail} available, "
              f"{with_url} with a detail URL) using the {strategy} parser.")
    elif "--dump" in argv:
        raw, parsed, how = fetch_listings()
        pathlib.Path("dump.html").write_text(raw)
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
        avail = sum(1 for r in parsed if r["status"] == "available")
        with_url = sum(1 for r in parsed if r["url"])
        print(f"\nURL: {KEREBY_URL}\nSaved raw HTML to dump.html "
              f"({len(raw)} bytes) -- fetched via {how}, parsed {len(parsed)} "
              f"listings ({avail} available, {with_url} with a detail URL).")
        print("\n--- one line per listing (check the statuses!) ---")
        for r in parsed:
            print(f'  {r["status"]:<10} {(r["address"] or "?")[:46]:<46} '
                  f'{(str(r["price"]) + " kr") if r["price"] else "-":>10} '
                  f'{(str(r["rooms"]) + " vær") if r["rooms"] else "-":>7} '
                  f'{(str(r["sqm"]) + " m2") if r["sqm"] else "-":>7} '
                  f'link:{"yes" if r["url"] else "NO "} '
                  f'{(r["title"] or "-")[:40]}')
        if not parsed:
            print("\n0 listings. Next: `python kereby_watch.py --probe` to test "
                  "other URLs, or open the page in a browser (DevTools -> "
                  "Network -> Fetch/XHR) to find a JSON API. If the page is "
                  "JS-rendered: pip install playwright && playwright install "
                  "chromium, then RENDER_JS=true.")
    elif "--test" in argv:
        from _selftest import main as t
        t()
    else:
        # A minute-by-minute cron can overlap a slow run; two copies diffing
        # the same state would double-notify. First one in wins, the other
        # simply steps aside (not an error -- the next tick is 60s away).
        lock = STATE_FILE.with_name(".kereby-watch.lock")
        lock.touch(exist_ok=True)
        with lock.open("r+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("Another run is still going; skipping this tick.")
                sys.exit(0)
            try:
                run()
            except SystemExit as e:
                if e.code not in (0, None):
                    record_failure(str(e.code) if e.code else "unknown error")
                raise
            except Exception as e:
                record_failure(f"{type(e).__name__}: {e}")
                raise
            record_success()
