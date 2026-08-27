# Prompt for an AI with unrestricted network access

The build environment for this watcher blocks `kereby.dk`, so one question was
never answered: how are the listings actually served? Paste everything below the
line into an AI assistant that can reach the open internet and run code, then
bring its answer back.

Do **not** give that assistant your Telegram bot token — it doesn't need it.

---

You have internet access and can run code. I need you to finish one piece of a
working Python project by testing it against a live website, and report back in a
form another engineer can act on directly.

## Context

`kereby-watch` is a scheduled Python scraper: every 60 seconds on a Raspberry Pi
it checks Kereby.dk (a large Copenhagen residential landlord) for rental
apartments that have become **available**, and sends a Telegram message. Every
part of it — parsing, change detection, state, notifications, deployment — is
written and tested offline. The one unverified piece is how to fetch the
listings, because the machine it was built on couldn't reach the site.

Stack: Python 3.11+, `requests` + `beautifulsoup4`. It runs on a Raspberry Pi
once a minute, so a headless browser (Playwright) is a last resort, not a first
choice.

The parser needs each listing as this exact dict:

```python
{
  "address":  "Stormgade 35, 4. th, 1555 København V",  # also the dedup key
  "status":   "available",     # "available" | "reserved" | "rented"
  "title":    "7-værelses lejlighed på Vesterbro",
  "price":    32209,           # int, kr./md.
  "rooms":    7,               # int
  "sqm":      203,             # int
  "features": ["Altan"],       # list[str]
  "url":      "https://kereby.dk/bolig/stormgade-35-4-th/",  # detail page
  "id":       "Stormgade 35, 4. th, 1555 København V",       # == address
}
```

Two things matter about that schema. `status == "available"` corresponds to a
listing showing **no** status line (units show `Status: Reserveret` or
`Status: Udlejet` when taken). And `id` must be **byte-identical across runs** —
it's the key that decides whether an apartment is newly available, so a stray
trailing word in the address would cause duplicate or missed alerts.

## What to do

1. **Find the real listings page.** The current guess is
   `https://kereby.dk/ledige-boliger/` and it may be wrong. Check the site's
   navigation and `sitemap.xml`. Report the final URL. If listings are paginated,
   report how to reach every page.
2. **Determine how it's rendered.** Fetch with plain `requests` (send a normal
   browser User-Agent) and check whether the listing data is actually in the
   returned HTML. Report the HTTP status, response size, and how many listings
   are visible to `requests`.
3. **If the HTML doesn't contain the listings**, find the data source: check
   DevTools-style XHR/fetch requests, and search the HTML for `__NEXT_DATA__`,
   embedded JSON blobs, `/api/`, `/wp-json/`, or GraphQL. Verify with `curl`
   that it works with no auth and no cookies. Report the method, URL, query
   params, any required headers, whether it paginates, and a pretty-printed
   sample of **one** listing object with its real field names.
4. **Only if there is no usable data endpoint**, confirm that a headless browser
   is genuinely required, and say precisely what wait condition makes the
   listings appear (a selector, `networkidle`, etc.).
5. **Capture the ground truth**, quoted exactly, not paraphrased:
   - total listings, and how many are available right now;
   - the exact Danish status strings (e.g. `Status: Reserveret`);
   - the exact price/rooms/area wording (e.g. `11.896 kr./md.`, `2 værelser`,
     `74 m²`) — including whether the thousands separator is `.` and the area
     unit is `m²`;
   - every feature word that appears (e.g. Altan, Elevator, Delevenlig,
     Tagterrasse, Penthouse);
   - the detail-page URL pattern.
6. **Write and actually run** a self-contained `fetch_listings()` that returns a
   list of those dicts for the live page. Print its output for every listing and
   verify three of them by eye against the site. Make sure it handles: an
   address split across multiple HTML tags, a unit with no status line, and a
   listing missing a price or features.
7. **Politeness and blocking.** Check `robots.txt` and say whether the listings
   path (or the API) is disallowed. Note any rate limiting, Cloudflare, or bot
   protection. The watcher polls once per minute from a single home IP — say
   whether that is likely to get blocked, and if so, what interval is safe.

## What to return

The person receiving this is on a phone, so be compact and paste-friendly. In
this order:

- **A. Findings** — final URL, rendering verdict (server-rendered / JSON API /
  needs a browser), listing counts, robots and rate-limit note. A few sentences.
- **B. One fenced Python block** — the complete working `fetch_listings()` plus
  any helpers and imports, exactly as you ran it. No placeholders, no
  pseudocode, nothing you didn't execute.
- **C. One fenced block of trimmed source** — the verbatim markup of the **first
  two listing cards only**, from the opening tag of the card container to its
  closing tag, 150 lines maximum. If the data comes from JSON, give one raw
  listing object verbatim instead. Do **not** paste the whole page.
- **D. One fenced JSON block** — the parsed output of all listings from your
  implementation.
- **E. Fragility** — what in your implementation would break on a site redesign,
  and what would be a more robust anchor.

Rules: quote exact strings rather than summarizing them; never invent field
names; don't return the entire page. If the site is unreachable or blocked from
your environment too, say so plainly instead of guessing — a wrong answer here
is worse than no answer.

The code is in this repository, so read it if you like — but everything you
need is above. Don't block on it.
