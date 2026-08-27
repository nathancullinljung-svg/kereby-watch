"""Offline self-test: proves parsing + new-available detection with no network."""
import os
import re
from bs4 import BeautifulSoup
from kereby_watch import (parse_listings_text, parse_listings_html, parse_page,
                          address_from_lines, detail_url_candidates,
                          parse_listings_jorato,
                          resolve_detail_url,
                          find_detail_url, find_new_available, format_message,
                          format_summary_message, passes_filters)

SAMPLE = """14 resultater

Allersgade 1, 2. th, 2200 København N
Status: Reserveret
2-værelses lejlighed på Nørrebro
11.896 kr./md.
2 værelser
74 m²
Altan

Christian IX's Gade 5, 2. tv, 1111 København K
4-værelses lejlighed i Indre by
32.938 kr./md.
4 værelser
155 m²
Elevator

Herninggade 26, st., 2100 København Ø
Status: Reserveret
4 vær. på Østerbro (Lav stuelejlighed)
21.600 kr./md.
4 værelser
96 m²
Delevenlig

Amagerbrogade 238A, 1. tv, 2300 København S
Status: Reserveret
2-værelses lejlighed på Amager
12.116 kr./md.
2 værelser
67 m²

Roskildevej 47, st., 2000 Frederiksberg
6-værelses lejlighed på Frederiksberg
34.667 kr./md.
6 værelser
160 m²
Delevenlig

Gammel Kongevej 138D, 1., 1850 Frederiksberg C
Status: Reserveret
5-værelses lejlighed på Frederiksberg
20.140 kr./md.
5 værelser
124 m²
Delevenlig

Nygårdsvej 16B, st. 3, 2100 København Ø
Status: Udlejet
1-værelses lejlighed på Østerbro
9.854 kr./md.
1 værelse
43 m²
Tagterrasse

Stormgade 35, 4. th, 1555 København V
7-værelses lejlighed på Vesterbro
OBS: Udlejes ikke til bofællesskaber
32.209 kr./md.
7 værelser
203 m²
Altan

Ved Klosteret 6, 1. tv, 2100 København Ø
Status: Reserveret
4-værelses lejlighed på Østerbro
17.676 kr./md.
4 værelser
108 m²

Nygårdsvej 16, 2. tv, 2100 København Ø
Status: Udlejet
3-værelses lejlighed på Østerbro
13.699 kr./md.
3 værelser
85 m²

Falkoner Alle 12B, 4. th, 2000 Frederiksberg
Status: Udlejet
5-værelses lejlighed på Frederiksberg
17.856 kr./md.
5 værelser
108 m²
Elevator

Strandboulevarden 61, 5. th, 2100 København Ø
4-værelses lejlighed på Østerbro
26.250 kr./md.
4 værelser
126 m²
Penthouse
Tagterrasse

Bjelkes Allé 18A, 5., 2200 København N
Status: Reserveret
2-værelses lejlighed tæt på metro og grønne områder
16.042 kr./md.
2 værelser
77 m²

Sortedam Dossering 45, 5. tv, 2200 København N
Fantastisk penthouselejlighed lige ved Søerne
25.650 kr./md.
3 værelser
114 m²
Penthouse
Tagterrasse
"""

# A miniature listings page in the shape a real one takes: each card wrapped in
# its own <a>, and the address split across two tags so it never lands on a
# single text line -- which is what the card parser exists to handle.
HTML_SAMPLE = """
<html><body>
<nav><a href="/">Forside</a><a href="/kontakt/">Kontakt</a></nav>
<main>
  <h1>Ledige boliger</h1><p>2 resultater</p>
  <a class="card" href="/bolig/stormgade-35-4-th/">
    <span class="street">Stormgade 35, 4. th,</span>
    <span class="city">1555 K&oslash;benhavn V</span>
    <p>7-v&aelig;relses lejlighed p&aring; Vesterbro</p>
    <span>32.209 kr./md.</span><span>7 v&aelig;relser</span>
    <span>203 m&sup2;</span><span>Altan</span>
  </a>
  <a class="card" href="https://kereby.dk/bolig/nygaardsvej-16-2-tv/">
    <span class="street">Nyg&aring;rdsvej 16, 2. tv,</span>
    <span class="city">2100 K&oslash;benhavn &Oslash;</span>
    <span>Status: Udlejet</span>
    <p>3-v&aelig;relses lejlighed p&aring; &Oslash;sterbro</p>
    <span>13.699 kr./md.</span><span>3 v&aelig;relser</span><span>85 m&sup2;</span>
  </a>
</main>
<footer><a href="/cookies/">Cookies</a><p>Persondatapolitik</p>
<p>Altan</p><p>CVR 12345678</p></footer>
</body></html>
"""

# Same two units, but server-rendered so each field is on its own text line
# (the shape the original text parser was written against), plus footer chrome
# that must not leak into the last listing.
TEXT_WITH_FOOTER = """2 resultater

Stormgade 35, 4. th, 1555 K\u00f8benhavn V
7-v\u00e6relses lejlighed p\u00e5 Vesterbro
32.209 kr./md.
7 v\u00e6relser
203 m\u00b2
Altan

Nyg\u00e5rdsvej 16, 2. tv, 2100 K\u00f8benhavn \u00d8
3-v\u00e6relses lejlighed p\u00e5 \u00d8sterbro
13.699 kr./md.
3 v\u00e6relser
85 m\u00b2

Cookies
Persondatapolitik
Elevator
Tagterrasse
"""


def test_text_parser():
    recs = parse_listings_text(SAMPLE)
    assert len(recs) == 14, f"expected 14, got {len(recs)}"
    avail = [r for r in recs if r["status"] == "available"]
    print(f"Parsed {len(recs)} listings, {len(avail)} available:")
    for r in avail:
        print(f"  - {r['address']}  ({r['rooms']} v\u00e6r, {r['sqm']} m\u00b2, "
              f"{r['price']} kr)")
    assert len(avail) == 5, f"expected 5 available, got {len(avail)}"

    chr9 = next(r for r in recs if r["id"].startswith("Christian"))
    assert chr9["price"] == 32938 and chr9["rooms"] == 4 and chr9["sqm"] == 155
    assert "Elevator" in chr9["features"]
    assert chr9["url"] is None, "text parsing alone cannot know detail URLs"
    print("  text parser OK")


def test_footer_is_not_absorbed():
    recs = parse_listings_text(TEXT_WITH_FOOTER)
    assert len(recs) == 2, f"expected 2, got {len(recs)}"
    last = recs[-1]
    assert last["features"] == [], f"footer leaked into features: {last['features']}"
    assert last["sqm"] == 85 and last["rooms"] == 3
    print("  footer cutoff OK")


def test_card_parser_and_links():
    soup = BeautifulSoup(HTML_SAMPLE, "html.parser")
    # The line parser must find nothing here (addresses are split across tags),
    # so parse_page has to fall back to the card parser.
    assert not parse_listings_text(soup.get_text(separator="\n")), \
        "line parser unexpectedly matched the split-address sample"

    recs, strategy = parse_page(HTML_SAMPLE, "https://kereby.dk/ledige-boliger/")
    assert strategy == "cards", strategy
    assert len(recs) == 2, [r["address"] for r in recs]

    storm = next(r for r in recs if r["address"].startswith("Stormgade"))
    assert storm["status"] == "available", storm["status"]
    # 203 m<sup>2</sup> on the live page: the area must survive the split tag.
    assert storm["price"] == 32209 and storm["rooms"] == 7, storm
    assert storm["sqm"] == 203, "area lost to a split sup tag: %r" % storm["sqm"]
    assert storm["features"] == ["Altan"], storm["features"]
    assert storm["title"] == "7-v\u00e6relses lejlighed p\u00e5 Vesterbro", storm["title"]
    assert storm["url"] == "https://kereby.dk/bolig/stormgade-35-4-th/", storm["url"]

    nyg = next(r for r in recs if r["address"].startswith("Nyg\u00e5rdsvej"))
    assert nyg["status"] == "rented", nyg["status"]
    # The address is the state key: it must be exact, with no neighbouring
    # word ("Status") swept in, or the same unit would get two state entries.
    assert nyg["id"] == "Nyg\u00e5rdsvej 16, 2. tv, 2100 K\u00f8benhavn \u00d8", nyg["id"]
    assert storm["id"] == "Stormgade 35, 4. th, 1555 K\u00f8benhavn V", storm["id"]
    assert nyg["url"] == "https://kereby.dk/bolig/nygaardsvej-16-2-tv/", nyg["url"]
    print(f"  card parser OK ({len(recs)} cards, both with detail URLs)")

    # Relative hrefs resolve against whatever URL we fetched.
    other = find_detail_url(soup, "Stormgade 35, 4. th, 1555 K\u00f8benhavn V",
                            "https://www.kereby.dk/en/vacancies/")
    assert other == "https://www.kereby.dk/bolig/stormgade-35-4-th/", other
    print("  relative-URL resolution OK")


def test_message_links_to_the_unit():
    recs, _ = parse_page(HTML_SAMPLE, "https://kereby.dk/ledige-boliger/")
    storm = next(r for r in recs if r["address"].startswith("Stormgade"))
    msg = format_message(storm)
    assert '<a href="https://kereby.dk/bolig/stormgade-35-4-th/">' in msg, msg
    assert "32.209 kr/md" in msg and "203 m\u00b2" in msg, msg
    assert msg.startswith("\U0001f3e0 <b>7-v\u00e6relses lejlighed p\u00e5 Vesterbro</b>"), msg
    print("  Telegram message links to the unit's own page OK")

    # No detail URL known -> fall back to the listings page, never crash.
    import kereby_watch as kw
    bare = dict(storm, url=None)
    assert f'<a href="{kw.KEREBY_URL}">' in format_message(bare), format_message(bare)
    print("  message URL fallback OK")


def test_message_escapes_html():
    """parse_mode=HTML means unescaped page text would break the send."""
    r = {"address": 'Store & Sm\u00e5 Gade 1 <b>, 2100 K\u00f8benhavn \u00d8',
         "title": 'Lejlighed "med" altan & <script>', "status": "available",
         "price": 12000, "rooms": 3, "sqm": 80, "features": [],
         "url": "https://kereby.dk/bolig/a?x=1&y=2"}
    msg = format_message(r)
    assert "<script>" not in msg and "&lt;script&gt;" in msg, msg
    assert "Store &amp; Sm\u00e5 Gade" in msg, msg
    assert 'href="https://kereby.dk/bolig/a?x=1&amp;y=2"' in msg, msg
    # Only the tags we add survive as real markup.
    assert msg.count("<b>") == 1 and msg.count("<a href=") == 1, msg
    print("  HTML escaping OK (title, address, and URL query separators)")


def test_filters():
    recs = parse_listings_text(SAMPLE)
    import os
    os.environ["MIN_ROOMS"] = "4"
    os.environ["MAX_PRICE"] = "30000"
    try:
        kept = [r for r in recs if passes_filters(r)]
        assert kept, "filters removed everything"
        assert all(r["rooms"] >= 4 and r["price"] <= 30000 for r in kept), \
            [(r["rooms"], r["price"]) for r in kept]
        os.environ["AREAS"] = "2100"
        kept2 = [r for r in recs if passes_filters(r)]
        assert kept2 and all("2100" in r["address"] for r in kept2)
    finally:
        for k in ("MIN_ROOMS", "MAX_PRICE", "AREAS"):
            os.environ.pop(k, None)
    print(f"  filters OK ({len(kept)} of {len(recs)} pass rooms>=4 & price<=30000)")


def test_summary_message():
    recs = [r for r in parse_listings_text(SAMPLE) if r["status"] == "available"]
    msg = format_summary_message(recs * 3)   # 15 hits
    assert "<b>15 nye ledige boliger hos Kereby</b>" in msg, msg
    assert "\u2026+9 flere" in msg, msg
    assert len(msg.splitlines()) <= 8, msg
    assert "<script" not in msg
    print("  summary message (burst cap) OK")


def test_diff_over_two_runs():
    recs = parse_listings_text(SAMPLE)
    print("\n--- Simulating two consecutive runs ---")
    # Run 1: seed state from current snapshot (no notifications on first run)
    state = {r["id"]: r["status"] for r in recs}
    print("Run 1: state seeded, 0 notifications (first run).")

    # Run 2: Allersgade flips Reserveret -> available; a new unit appears.
    recs2 = parse_listings_text(SAMPLE.replace(
        "Allersgade 1, 2. th, 2200 K\u00f8benhavn N\nStatus: Reserveret",
        "Allersgade 1, 2. th, 2200 K\u00f8benhavn N"))
    recs2.append({"id": "Nyvej 9, 3. tv, 2100 K\u00f8benhavn \u00d8",
                  "address": "Nyvej 9, 3. tv, 2100 K\u00f8benhavn \u00d8",
                  "status": "available", "title": "3-v\u00e6r p\u00e5 \u00d8sterbro",
                  "price": 15000, "rooms": 3, "sqm": 82, "features": [],
                  "url": "https://kereby.dk/bolig/nyvej-9-3-tv/"})
    hits = find_new_available(recs2, state)
    print(f"Run 2: {len(hits)} notification(s):")
    for h in hits:
        print("  >>> message would be:")
        for line in format_message(h).splitlines():
            print("      " + line)
    ids = {h["id"] for h in hits}
    assert ids == {"Allersgade 1, 2. th, 2200 K\u00f8benhavn N",
                   "Nyvej 9, 3. tv, 2100 K\u00f8benhavn \u00d8"}, ids
    # Christian IX was already available in run 1 -> must NOT re-notify
    assert not any("Christian" in i for i in ids)

    # Run 3: nothing changed since run 2 -> silence.
    state.update({r["id"]: r["status"] for r in recs2})
    assert find_new_available(recs2, state) == [], "re-notified an unchanged run"
    print("Run 3: no changes, 0 notifications.")

    # Run 4: a unit goes Reserveret and then comes back -> one alert each time.
    flipped = [dict(r, status="reserved") if "Nyvej" in r["id"] else r
               for r in recs2]
    state.update({r["id"]: r["status"] for r in flipped})
    assert [h["id"] for h in find_new_available(recs2, state)] == \
        ["Nyvej 9, 3. tv, 2100 K\u00f8benhavn \u00d8"]
    print("Run 4: reserved -> available flip re-alerts exactly once.")


def test_run_end_to_end():
    """Drive run() with a fake fetch + fake sender: guards, state, no dupes."""
    import os, json, tempfile, pathlib as _pl
    import kereby_watch as kw

    recs = parse_listings_text(SAMPLE)
    sent, mode = [], {"records": recs}
    real_fetch, real_send, real_state = (kw.fetch_listings, kw.send_telegram,
                                        kw.STATE_FILE)
    tmp = _pl.Path(tempfile.mkdtemp()) / "seen.json"
    kw.STATE_FILE = tmp
    kw.fetch_listings = lambda url=None: ("<html/>", mode["records"], "fake")
    kw.send_telegram = lambda msg: sent.append(msg)
    try:
        kw.run()                                    # first run: seed only
        state = json.loads(tmp.read_text())
        assert sent == [], sent
        assert len(state) == 14, len(state)

        mode["records"] = parse_listings_text(SAMPLE.replace(
            "Herninggade 26, st., 2100 K\u00f8benhavn \u00d8\nStatus: Reserveret",
            "Herninggade 26, st., 2100 K\u00f8benhavn \u00d8"))
        kw.run()                                    # a flip: exactly one message
        assert len(sent) == 1 and "Herninggade" in sent[0], sent
        kw.run()                                    # same page again: silence
        assert len(sent) == 1, sent

        # A send that blows up must still leave the successful ones recorded,
        # so the next run does not re-text them.
        mode["records"] = parse_listings_text(SAMPLE.replace(
            "Status: Reserveret", "").replace("Status: Udlejet", ""))
        boom = {"n": 0}
        def flaky(msg):
            boom["n"] += 1
            if boom["n"] > 2:
                raise RuntimeError("telegram down")
            sent.append(msg)
        kw.send_telegram = flaky
        os.environ["MAX_ALERTS_PER_RUN"] = "99"
        try:
            kw.run()
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected the failing send to propagate")
        before = len(sent)
        kw.send_telegram = lambda msg: sent.append(msg)
        kw.run()                                    # recovery run
        assert len(sent) > before, "recovery run sent nothing"
        n = len(sent); kw.run()
        assert len(sent) == n, "re-notified after everything was recorded"

        # Empty fetch must fail loudly and leave state alone.
        snapshot = tmp.read_text()
        mode["records"] = []
        try:
            kw.run()
        except SystemExit as e:
            assert "0 listings" in str(e), e
        else:
            raise AssertionError("empty fetch did not fail the run")
        assert tmp.read_text() == snapshot, "state was clobbered by an empty fetch"
        print(f"  run() end-to-end OK ({len(sent)} messages over 7 runs, "
              f"state survives failures)")
    finally:
        os.environ.pop("MAX_ALERTS_PER_RUN", None)
        (kw.fetch_listings, kw.send_telegram,
         kw.STATE_FILE) = real_fetch, real_send, real_state


def test_address_reconstruction():
    """The address must come out identical however the page splits it."""
    want = "Stormgade 35, 4. th, 1555 K\u00f8benhavn V"
    # whole address on one line
    assert address_from_lines([want, "32.209 kr./md."])[0] == want
    # split with a trailing comma on the street fragment
    assert address_from_lines(
        ["Stormgade 35, 4. th,", "1555 K\u00f8benhavn V", "7 v\u00e6relser"])[0] == want
    # split without the trailing comma
    assert address_from_lines(
        ["Stormgade 35, 4. th", "1555 K\u00f8benhavn V"])[0] == want
    # a preceding title must not be glued on, and neither must a status line
    addr, used = address_from_lines(
        ["7-v\u00e6relses lejlighed p\u00e5 Vesterbro", "Stormgade 35, 4. th,",
         "1555 K\u00f8benhavn V", "Status: Udlejet"])
    assert addr == want, addr
    assert "Status: Udlejet" not in used and \
        "7-v\u00e6relses lejlighed p\u00e5 Vesterbro" not in used, used
    # nothing address-shaped at all
    assert address_from_lines(["12.000 kr./md.", "3 v\u00e6relser"])[0] is None
    print("  address reconstruction OK (one-line, split, split-no-comma)")


def test_card_parser_no_trailing_comma():
    html = HTML_SAMPLE.replace("Stormgade 35, 4. th,</span>",
                               "Stormgade 35, 4. th</span>")
    recs, strategy = parse_page(html, "https://kereby.dk/ledige-boliger/")
    assert strategy == "cards" and len(recs) == 2, (strategy, len(recs))
    storm = next(r for r in recs if r["address"].startswith("Stormgade"))
    assert storm["id"] == "Stormgade 35, 4. th, 1555 K\u00f8benhavn V", storm["id"]
    assert storm["title"] == "7-v\u00e6relses lejlighed p\u00e5 Vesterbro", storm["title"]
    print("  card parser handles a street line with no trailing comma")


def test_telegram_retry_and_errors():
    """Telegram delivery: retry the transient, report the permanent."""
    import kereby_watch as kw

    class Resp:
        def __init__(self, code, body="", payload=None):
            self.status_code, self.text = code, body
            self.ok = code < 400
            self._payload = payload or {}
        def json(self):
            return self._payload

    real_post, real_sleep = kw.requests.post, kw.time.sleep
    os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"
    os.environ["TELEGRAM_CHAT_ID"] = "42"
    slept, calls = [], []
    kw.time.sleep = slept.append
    try:
        # 429 with retry_after, then success: honours Telegram's own delay.
        queue = [Resp(429, "slow down", {"parameters": {"retry_after": 7}}),
                 Resp(200, "", {"result": {"message_id": 5}})]
        kw.requests.post = lambda url, **kw_: (calls.append(url), queue.pop(0))[1]
        kw.send_telegram("hi")
        assert slept == [7], slept
        assert len(calls) == 2, calls

        # 5xx then success: exponential backoff.
        slept.clear()
        queue = [Resp(503, "bad gateway"), Resp(200, "", {"result": {}})]
        kw.send_telegram("hi")
        assert slept == [1], slept

        # 400 (bad chat id / broken HTML) is permanent: fail fast, quote it.
        slept.clear()
        queue = [Resp(400, '{"description":"chat not found"}')]
        try:
            kw.send_telegram("hi")
        except SystemExit as e:
            assert "chat not found" in str(e) and "400" in str(e), e
        else:
            raise AssertionError("a 400 should not be retried or swallowed")
        assert slept == [], "retried a permanent error"

        # Connection errors exhaust the attempts, then report cleanly.
        def boom(url, **kw_):
            raise kw.requests.ConnectionError("no route")
        kw.requests.post = boom
        slept.clear()
        try:
            kw.send_telegram("hi")
        except SystemExit as e:
            assert "after 3 attempts" in str(e) and "no route" in str(e), e
        else:
            raise AssertionError("expected SystemExit after exhausting retries")
        assert slept == [1, 2], slept

        # Missing credentials are caught before any network call.
        os.environ.pop("TELEGRAM_CHAT_ID")
        try:
            kw.send_telegram("hi")
        except SystemExit as e:
            assert "TELEGRAM_CHAT_ID" in str(e), e
        else:
            raise AssertionError("missing chat id should abort")
        print("  Telegram retry/backoff, permanent-error and env guards OK")
    finally:
        kw.requests.post, kw.time.sleep = real_post, real_sleep
        for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            os.environ.pop(k, None)


def test_health_reporting():
    """Unattended runs must report their own breakage, exactly once."""
    import tempfile, pathlib as _pl
    import kereby_watch as kw

    real_send, real_state = kw.send_telegram, kw.STATE_FILE
    kw.STATE_FILE = _pl.Path(tempfile.mkdtemp()) / "seen.json"
    msgs = []
    kw.send_telegram = lambda m: msgs.append(m)
    os.environ["FAIL_ALERT_AFTER"] = "3"
    os.environ["FAIL_ALERT_HOURS"] = "6"
    try:
        t = 1_000_000.0
        # Below the threshold: count quietly, a blip is not news.
        kw.record_failure("boom", now=t); kw.record_failure("boom", now=t + 60)
        assert msgs == [], msgs
        # Third strike: one warning.
        kw.record_failure("0 listings parsed", now=t + 120)
        assert len(msgs) == 1 and "not working" in msgs[0], msgs
        assert "0 listings parsed" in msgs[0], msgs
        # Every minute after that: silence until the cooldown elapses.
        for k in range(1, 60):
            kw.record_failure("0 listings parsed", now=t + 120 + k * 60)
        assert len(msgs) == 1, f"repeated the warning {len(msgs)}x"
        kw.record_failure("0 listings parsed", now=t + 7 * 3600)
        assert len(msgs) == 2, "never repeated the warning after the cooldown"
        # Recovery says so once, then goes quiet.
        kw.record_success(now=t + 8 * 3600)
        assert len(msgs) == 3 and "working again" in msgs[2], msgs
        kw.record_success(now=t + 8 * 3600 + 60)
        assert len(msgs) == 3, "repeated the recovery message"
        # A failure alert that can't be delivered must not crash the run.
        kw.send_telegram = lambda m: (_ for _ in ()).throw(SystemExit("no net"))
        for k in range(3):
            kw.record_failure("offline", now=t + 9 * 3600 + k)
        print(f"  health reporting OK ({len(msgs)} messages over ~9h of failures)")
    finally:
        kw.send_telegram, kw.STATE_FILE = real_send, real_state
        for k in ("FAIL_ALERT_AFTER", "FAIL_ALERT_HOURS"):
            os.environ.pop(k, None)


def test_inspect_page():
    """--inspect must find the card markup, and explain an empty page."""
    import io, contextlib
    import kereby_watch as kw

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        kw.inspect_page(HTML_SAMPLE, None, "https://kereby.dk/ledige-boliger/", 900)
    text = out.getvalue()
    assert "Parsed 2 listings" in text, text
    assert 'href=/bolig/stormgade-35-4-th/' in text, text
    assert "Smallest element containing one full listing" in text, text
    # The outline is angle-bracket-free on purpose: HTML tags do not survive
    # being read back out of a GitHub issue body.
    assert "a.card href=/bolig/stormgade-35-4-th/" in text, text
    assert "span.street" in text and "Stormgade 35, 4. th," in text, text
    assert "<" not in text and ">" not in text, "outline must avoid tags"
    assert "Forside" not in text, "printed more than the one card"

    # A JavaScript shell: no listings, so report what the page loads instead.
    shell = ('<html><head><script src="/_next/static/chunks/main.js"></script>'
             '</head><body><div id="__next"></div>'
             '<script id="__NEXT_DATA__" type="application/json">'
             '{"props":{}}</script></body></html>')
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        kw.inspect_page(shell, None, "https://kereby.dk/ledige-boliger/")
    text = out.getvalue()
    assert "Parsed 0 listings" in text, text
    assert "__NEXT_DATA__" in text and "main.js" in text, text
    assert "rendered client-side" in text, text
    print("  --inspect reports card markup, and JS markers when there are none")


def test_detail_url_derivation():
    """A derived link is only used once the server confirms it exists."""
    import kereby_watch as kw

    # The first candidate matches the URLs observed on the live site.
    cands = detail_url_candidates("Stormgade 35, 4. th, 1555 K\u00f8benhavn V")
    assert cands[0] == "https://kereby.dk/bolig/stormgade-35-4-th-1555-kobenhavn-v/", cands
    cands = detail_url_candidates("Christian IX's Gade 5, 2. tv, 1111 K\u00f8benhavn K")
    assert cands[0] == \
        "https://kereby.dk/bolig/christian-ixs-gade-5-2-tv-1111-kobenhavn-k/", cands
    # Both Danish transliteration schemes are offered for å/ø.
    cands = detail_url_candidates("Nyg\u00e5rdsvej 16, 2. tv, 2100 K\u00f8benhavn \u00d8")
    assert any("nygardsvej" in c and "kobenhavn-o/" in c for c in cands), cands
    assert any("nygaardsvej" in c and "koebenhavn-oe/" in c for c in cands), cands

    real_head, real_get = kw.requests.head, kw.requests.get
    asked = []

    class Resp:
        def __init__(self, code): self.status_code = code

    try:
        # Only the second scheme exists -> that's the one we use.
        def head(url, **kw_):
            asked.append(url)
            return Resp(200 if "koebenhavn-oe" in url else 404)
        kw.requests.head = head
        kw._url_cache.clear()
        r = {"address": "Nyg\u00e5rdsvej 16, 2. tv, 2100 K\u00f8benhavn \u00d8", "url": None}
        got = resolve_detail_url(r)
        assert got and "koebenhavn-oe" in got, got
        assert r["url"] == got
        assert len(asked) == 2, asked

        # Second time the same address is free: cached, no more requests.
        asked.clear()
        r2 = {"address": r["address"], "url": None}
        assert resolve_detail_url(r2) == got and asked == [], asked

        # Nothing exists -> stay None so the message links to the listings page.
        kw.requests.head = lambda url, **kw_: Resp(404)
        kw._url_cache.clear()
        r3 = {"address": "Ukendt Vej 1, 9999 Ingensted", "url": None,
              "title": "x", "rooms": None, "sqm": None, "price": None}
        assert resolve_detail_url(r3) is None
        assert format_message(r3).endswith('>Se boligen p\u00e5 Kereby</a>')
        assert kw.KEREBY_URL in format_message(r3)

        # A network failure must not break the run.
        def boom(url, **kw_):
            raise kw.requests.ConnectionError("down")
        kw.requests.head = boom
        kw._url_cache.clear()
        assert resolve_detail_url({"address": r["address"], "url": None}) is None

        # An anchor found on the page always wins; no request at all.
        asked.clear()
        kw.requests.head = head
        assert resolve_detail_url({"address": r["address"],
                                   "url": "https://kereby.dk/bolig/from-page/"}) \
            == "https://kereby.dk/bolig/from-page/"
        assert asked == [], asked
        print("  detail-URL derivation OK (verified before use, cached, safe on error)")
    finally:
        kw.requests.head, kw.requests.get = real_head, real_get
        kw._url_cache.clear()


# Rebuilt from the real kereby.dk/bolig/ markup captured by the recon workflow
# (issue #1): status is a bare badge word BEFORE the address, cards carry
# jorato-case-card--unavailable when taken, and taken cards are not linked.
JORATO_SAMPLE = """
<div class="jorato-case-results__stage"><div class="jorato-case-grid">
  <div class="jorato-case-grid__col jorato-case-grid__col--1">
    <div class="jorato-case-card jorato-case-card--unavailable">
      <div class="jorato-case-card__link">
        <span class="jorato-case-card__badge">Reserveret</span>
        <img alt="" src="https://media.jorato.com/images/case/aaa/main.webp"/>
        <img alt="" src="https://media.jorato.com/images/case/bbb/main.webp"/>
        <div class="jorato-case-card__main">
          <p class="jorato-case-card__location">
            <span class="jorato-case-card__location-text">Amagerbrogade 120, 3. tv, 2300 K&oslash;benhavn S</span>
          </p>
          <p class="jorato-case-card__title">2-v&aelig;relses lejlighed p&aring; Amager</p>
          <span>12.500 kr./md.</span><span>2 v&aelig;relser</span><span>68 m&sup2;</span>
          <span>Altan</span>
        </div>
      </div>
    </div>
  </div>
  <div class="jorato-case-grid__col jorato-case-grid__col--1">
    <div class="jorato-case-card">
      <a class="jorato-case-card__link" href="/bolig/stormgade-35-4-th-1555-kobenhavn-v/">
        <img alt="" src="https://media.jorato.com/images/case/ccc/main.webp"/>
        <div class="jorato-case-card__main">
          <p class="jorato-case-card__location">
            <span class="jorato-case-card__location-text">Stormgade 35, 4. th, 1555 K&oslash;benhavn V</span>
          </p>
          <p class="jorato-case-card__title">7-v&aelig;relses lejlighed p&aring; Vesterbro</p>
          <span>32.209 kr./md.</span><span>7 v&aelig;relser</span><span>203 m<sup>2</sup></span>
          <span>Altan</span><span>Se boligen</span>
        </div>
      </a>
    </div>
  </div>
  <div class="jorato-case-grid__col jorato-case-grid__col--1">
    <div class="jorato-case-card jorato-case-card--unavailable">
      <div class="jorato-case-card__link">
        <span class="jorato-case-card__badge">Udlejet</span>
        <div class="jorato-case-card__main">
          <p class="jorato-case-card__location">
            <span class="jorato-case-card__location-text">Nyg&aring;rdsvej 16, 2. tv, 2100 K&oslash;benhavn &Oslash;</span>
          </p>
          <p class="jorato-case-card__title">3-v&aelig;relses lejlighed p&aring; &Oslash;sterbro</p>
          <span>13.699 kr./md.</span><span>3 v&aelig;relser</span><span>85 m&sup2;</span>
        </div>
      </div>
    </div>
  </div>
</div></div>
"""


def test_jorato_parser():
    """The live page's real shape: badge words, before the address, per card."""
    recs, strategy = parse_page(JORATO_SAMPLE, "https://kereby.dk/bolig/")
    assert strategy == "jorato", strategy
    assert len(recs) == 3, [r["address"] for r in recs]
    by_street = {r["address"].split(",")[0]: r for r in recs}

    # This is the bug the recon caught: a flat-text parser reports all three
    # as available, because each badge precedes its own address.
    assert [r["status"] for r in recs] == ["reserved", "available", "rented"], \
        [(r["address"], r["status"]) for r in recs]
    assert sum(1 for r in recs if r["status"] == "available") == 1

    storm = by_street["Stormgade 35"]
    assert storm["address"] == "Stormgade 35, 4. th, 1555 K\u00f8benhavn V", storm["address"]
    # 203 m<sup>2</sup> on the live page: the area must survive the split tag.
    assert storm["price"] == 32209 and storm["rooms"] == 7, storm
    assert storm["sqm"] == 203, "area lost to a split sup tag: %r" % storm["sqm"]
    assert storm["features"] == ["Altan"], storm["features"]
    assert storm["title"] == "7-v\u00e6relses lejlighed p\u00e5 Vesterbro", storm["title"]
    assert storm["url"] == \
        "https://kereby.dk/bolig/stormgade-35-4-th-1555-kobenhavn-v/", storm["url"]

    amager = by_street["Amagerbrogade 120"]
    assert amager["title"] == "2-v\u00e6relses lejlighed p\u00e5 Amager", amager["title"]
    assert amager["price"] == 12500 and amager["sqm"] == 68
    # Taken cards aren't linked; nothing may be invented for them here.
    assert amager["url"] is None, amager["url"]
    # The badge word must never leak into the description or the features.
    for r in recs:
        assert r["title"] and r["title"].lower() not in ("reserveret", "udlejet")
        assert not any(f.lower() in ("reserveret", "udlejet", "se boligen")
                       for f in r["features"]), r["features"]

    # A card marked unavailable with no badge word still must not read available.
    stripped = JORATO_SAMPLE.replace(
        '<span class="jorato-case-card__badge">Reserveret</span>', "")
    amager2 = next(r for r in parse_listings_jorato(
        BeautifulSoup(stripped, "html.parser"), "https://kereby.dk/bolig/")
        if r["address"].startswith("Amagerbrogade"))
    assert amager2["status"] == "reserved", amager2["status"]
    print("  jorato parser OK (1 of 3 available, badge read per card)")


def test_setup_check():
    """--setup: discover the chat, deliver the id there, verify when set."""
    import io, contextlib
    import kereby_watch as kw

    class Resp:
        def __init__(self, code, payload):
            self.status_code, self.ok, self._p, self.text = code, code < 400, payload, ""
        def json(self): return self._p

    real_post = kw.requests.post
    os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"
    sent = []
    updates = [{"message": {"chat": {"id": 76543210, "first_name": "Tester"}}}]

    def post(url, **kw_):
        if url.endswith("/getMe"):
            return Resp(200, {"result": {"username": "kereby_test_bot",
                                         "first_name": "Kereby"}})
        if url.endswith("/getWebhookInfo"):
            return Resp(200, {"result": {"url": ""}})
        if url.endswith("/getUpdates"):
            return Resp(200, {"result": updates})
        sent.append((kw_["json"]["chat_id"], kw_["json"]["text"]))
        return Resp(200, {"result": {"message_id": 1}})

    try:
        kw.requests.post = post
        # No chat id configured: the id is sent to the chat we discovered.
        os.environ.pop("TELEGRAM_CHAT_ID", None)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            assert kw.setup_check() == "id_sent"
        assert "@kereby_test_bot" in out.getvalue(), out.getvalue()
        assert len(sent) == 1, sent
        chat, text = sent[0]
        assert chat == "76543210", chat
        assert "76543210" in text and "TELEGRAM_CHAT_ID" in text, text

        # Configured: send a test message to it, discover nothing.
        os.environ["TELEGRAM_CHAT_ID"] = "76543210"
        sent.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            assert kw.setup_check() == "verified"
        assert len(sent) == 1 and "connected" in sent[0][1], sent

        # Bot made but never messaged: a state to explain, not a crash. The
        # guidance must name the exact bot, since messaging the wrong one is
        # the most common cause.
        os.environ.pop("TELEGRAM_CHAT_ID", None)
        updates.clear()
        sent.clear()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            assert kw.setup_check() == "no_chats"
        guidance = out.getvalue()
        assert "https://t.me/kereby_test_bot" in guidance, guidance
        assert "START" in guidance and "never received a message" in guidance
        assert sent == [], sent

        # A webhook also empties getUpdates -- say so rather than blaming Start.
        def with_hook(url, **kw_):
            if url.endswith("/getWebhookInfo"):
                return Resp(200, {"result": {"url": "https://example.com/hook"}})
            return post(url, **kw_)
        kw.requests.post = with_hook
        with contextlib.redirect_stdout(io.StringIO()) as out:
            assert kw.setup_check() == "no_chats"
        assert "webhook" in out.getvalue(), out.getvalue()
        kw.requests.post = post

        # A chat id passed explicitly must not need the env var.
        os.environ.pop("TELEGRAM_CHAT_ID", None)
        with contextlib.redirect_stdout(io.StringIO()):
            kw.send_telegram("hi", chat_id="999")
        assert sent[-1][0] == "999", sent
        print("  --setup OK (discovers the chat, delivers the id, then verifies)")
    finally:
        kw.requests.post = real_post
        for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            os.environ.pop(k, None)


def main():
    test_text_parser()
    test_footer_is_not_absorbed()
    test_jorato_parser()
    test_address_reconstruction()
    test_card_parser_and_links()
    test_card_parser_no_trailing_comma()
    test_inspect_page()
    test_message_links_to_the_unit()
    test_detail_url_derivation()
    test_message_escapes_html()
    test_filters()
    test_summary_message()
    test_telegram_retry_and_errors()
    test_setup_check()
    test_diff_over_two_runs()
    test_run_end_to_end()
    test_health_reporting()
    print("\nALL ASSERTIONS PASSED \u2705")


if __name__ == "__main__":
    main()
