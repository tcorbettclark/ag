# Being blocked by Amazon: what happened, what works, what to do

Status: evaluation, 2026-11-05. **Tier 0, Tier 1 and 2.1 are implemented**
(see `## 5. What was implemented`); 2.2-2.5 are still proposals.

## 1. The last run was not blocked by Amazon

Run of 2026-11-04 16:16 → 21:21 (14 h budget, ended after 5.08 h) reports
`end_reason = "blocked repeatedly; resumable, run again later"`.

The full log contains exactly **one** block event, and it is a false positive:

```
20:15:10 WARNING block detected (status 404) on https://www.amazon.co.uk/dp/B0HP6MNDX8
20:15:10 WARNING cooling down 5 min then new session
20:20:23 WARNING block detected (status 404) on https://www.amazon.co.uk/dp/B0HP6MNDX8
20:20:23 WARNING cooling down 15 min then new session
20:36:03 WARNING block detected (status 404) on https://www.amazon.co.uk/dp/B0HP6MNDX8
20:36:03 WARNING cooling down 45 min then new session
21:21:17 WARNING block detected (status 404) on https://www.amazon.co.uk/dp/B0HP6MNDX8
21:21:17 WARNING run stopped: blocked repeatedly
```

`B0HP6MNDX8` is a dead ASIN. Verified live: `/dp/B0HP6MNDX8` returns **404,
2.2 kB**, and the body is Amazon's standard 404:

> We're sorry. The Web address you entered is not a functioning page on our site.

That page also carries the boilerplate line

> To discuss automated access to Amazon data please contact api-services-support@amazon.com.

which is in `BLOCK_MARKERS`. So `_is_blocked()` fires on every dead ASIN, the
retry ladder (5 → 15 → 45 min) reruns the *same dead URL*, and the fourth
failure ends the run.

Cost: **66 min of cooldown plus 9 h of unused budget**, with 687 ASINs (≈3.4 h
at the observed rate) still queued. Nothing was rate-limited, no captcha was
ever served.

Worse, it is recurring: on `StopRun` the ASIN stays in `in_flight`, is
re-appended to `pending` on the next load, and will end the *next* full sweep
the same way.

## 2. What Amazon actually uses against a crawler like this

Ranked by how likely each is to be what hits you, and what in this codebase
trips it.

| # | Mechanism | Trips on us? | Notes |
|---|---|---|---|
| 1 | Dead/removed ASIN → 404 page with bot boilerplate | **Yes, already did** | Not a block at all. Must never be classified as one. |
| 2 | IP / ASN rate limiting (429, or 503 + dog page) | Not yet, but this is the real ceiling | 3.4 navs/min sustained from one residential IP. Amazon's per-IP budget is not published; a few thousand pages/night is in the plausible-tolerated range, tens of thousands is not. |
| 3 | Browser fingerprint (`HeadlessChrome`, `navigator.plugins = 0`, no PDF viewer, SwiftShader WebGL, CDP artefacts) | **Yes** | `headless=True` uses Playwright's *headless shell*: `plugins=0`, `pdfViewerEnabled=false`, `window.chrome` undefined. Verified locally. |
| 4 | UA vs UA-Client-Hints version mismatch | **Yes** | `USER_AGENTS` hardcodes Chrome **129–131**; the installed Playwright Chromium is **156**. Every request claims 131; `navigator.userAgentData.brands` says 156. One-line fix, real signal. |
| 5 | Session/cookie state: new `session-id` every 20–35 navs | Mildly | A cold cookie jar every 25 navs looks *less* human than a stable one. Fresh cookies help only to escape an already-tainted session. |
| 6 | Navigation shape: 600 consecutive direct `/dp/` hits, no referrer, no search→product click path | **Yes** | Real users arrive at a product page *from* a search or a link (`Referer` header set, `document.referrer` non-empty). |
| 7 | Cadence regularity (4–10 s with occasional 20–45 s) | Low | Already reasonably human. Fixed-ish per-nav shape (goto → scroll 1–3× → pause) is more of a tell than the timing. |
| 8 | Captcha interstitial ("Enter the characters you see below") | Not yet | The hard block. If it appears, the IP is already scored; back off for hours, not minutes. |
| 9 | Silent throttling: short search pages, degraded/partial HTML | **Yes, silently** | `run_task` treats "no ASINs on page" as "search exhausted" → `done=True` → skipped for 7 days. A throttled page permanently poisons search progress. |

Note the two failure modes that matter are *opposite*: false positives (stop
for no reason) and silent corruption (believe a bad page). Both are cheap to
fix and both are more likely to bite than a real ban.

## 3. Options

### Tier 0 — stop the self-inflicted stops (highest value, low effort)

0.1 **Status-code triage.** 404/410/451 → record as `excluded_dead_asin`
    (or `unresolved_dead`), never a block, no cooldown, next ASIN. Only
    429/503 and *positive* captcha detection count as blocks.

0.2 **Fix `BLOCK_MARKERS`.** Drop `api-services-support@amazon.com` and
    `to discuss automated access`: they are on every 404. Keep
    `enter the characters you see below`, `to continue, please type the
    characters`, `api-services-support` **only when combined with** a captcha
    marker. Add a positive page check instead: if `#productTitle` /
    `#dp-container` is absent on a 200 of plausible size, it's "unexpected
    page", not "blocked".

0.3 **Per-ASIN give-up counter.** Track attempts per ASIN; after 3 failures
    mark `unresolved_<reason>`, drop from pending, continue. One bad ASIN must
    never be able to end a run, let alone three runs.

0.4 **Cooldown ladder must not retry the same URL**, must be capped (e.g.
    20 min per run), and after a cooldown the run continues with *different*
    ASINs. Reserve multi-hour backoff for real captcha detection and make it
    end the run *cleanly* rather than burning the budget sleeping.

0.5 **Don't mark a search done on an empty page** unless the page is
    positively "no results"/past-the-last-page. Otherwise record a
    `suspect_page` and leave `next_page` untouched.

Expected effect: the 2026-11-04 run would have completed its 14 h.

### Tier 1 — remove fingerprint tells (cheap, one-off)

1.1 Derive the UA from `browser.version` instead of the hardcoded list
    (`Chrome/<real>`, correct platform string). Removes the 131-vs-156
    mismatch.

1.2 `chromium.launch(channel="chrome")` — real Google Chrome 156 is installed.
    Verified: `plugins=5`, `pdfViewerEnabled=true`, `window.chrome` object,
    WebGL `ANGLE Metal Renderer: Apple M4` instead of SwiftShader. Playwright's
    bundled *headless shell* leaks all four.

1.3 Keep the cookie jar on disk (`storage_state`), reuse it, and refresh only
    when a block is detected. Rotation on a timer is currently backwards.

1.4 Set a `Referer`: before each `/dp/` hit, "arrive" from a search page for
    that ASIN's title (which you already have in `sources`). Free, and it makes
    the single deepest pattern in the traffic disappear.

1.5 Shuffle the backlog instead of FIFO, so successive runs don't walk the
    same prefix of ASINs in the same order.

### Tier 2 — change the shape of the crawl (bigger gains, more work)

2.1 **Pre-filter at enumeration.** 1463 of 3862 product fetches (38 %) were
    pages that aren't Christie books at all — sponsored cards, related-items
    carousels, Prime Video, foreign editions. Search result cards carry title,
    byline, format and price; capture `(asin, card_text)` when enumerating and
    drop obvious non-Christie/non-book cards before queueing. Cuts total
    navigations (and therefore block exposure) by roughly a third.

2.2 **Search-by-ASIN as a liveness probe.** Verified: `s?k=<asin>` returns
    **200 with "No results for"** for a dead ASIN, and the normal product card
    for a live one. A search page never 404s, and it looks like ordinary
    browsing. It does *not* carry Publisher/Language, so it cannot replace the
    `/dp/` fetch for classification — but as a fallback after a `/dp/` 404 it
    cleanly distinguishes "dead" from "not available", and as a first probe for
    ASINs that already failed once it removes the whole failure mode.

2.3 **Interleave searches into the classify phase** (e.g. one search page per
    15 product pages). You already alternate by phase, but inside a phase it
    is 600 identical `/dp/` hits in a row. Interleaving also refreshes the
    referrer chain naturally and keeps enumeration moving.

2.4 **ISBN-10 side-door for print editions.** 1928 of 3862 records are
    ISBN-10 ASINs (print ASIN *is* the ISBN-10). OpenLibrary / Google Books /
    isbnsearch return publisher + language with no anti-bot at all. Use them as
    a *pre-filter* only — definitely-HarperCollins or definitely-foreign →
    skip the Amazon fetch; everything else still goes to Amazon, because
    Google Books metadata is patchy enough to be unsafe as a decider. Roughly
    a third of print fetches (the 536 `excluded_not_english` are
    disproportionately foreign ISBNs) never touch Amazon.

2.5 **Slow down, deliberately.** 3.4 navs/min is already modest; 1.5–2/min
    halves nightly volume but is the single most effective lever against the
    mechanism that will eventually actually stop you (#2, IP rate). Given the
    backlog is ~3.4 h of work, this costs one extra night, not a week.

### Tier 3 — options I'd not take

- Residential proxies / IP rotation: works, but it's a deliberate evasion
  arms race, recurring cost, and the ToS position gets worse rather than
  better. The volume here doesn't need it.
- Logging in to a real Amazon account: makes throttling *account*-scoped and
  risks the account. Strictly worse.
- Solving captchas (2captcha etc.): same category as proxies.
- Scraping Google/Bing results for ASINs instead of Amazon search: Google will
  block you too, and you lose the buy-box data.

## 4. What I would do, in order

1. Tier 0.1–0.5 — this alone would have saved the last run, and protects
   against the *silent* corruption which is the bigger long-term threat.
2. Tier 1.1 + 1.2 + 1.4 — ~20 lines, removes the two verified fingerprint
   tells and the "no referrer" pattern.
3. Tier 2.1 — a third fewer navigations, i.e. a third less exposure.
4. Tier 2.3, then re-measure. Only if blocks still appear: Tier 2.5.
5. Keep Tier 2.2 as the fallback path for `/dp/` 404s.

## 5. What was implemented

| item | where |
|---|---|
| 0.1 404/410 -> `excluded_dead_asin`, never a block, never retried | `Crawler.goto`, `search.classify_pending` |
| 0.2 captcha-only `BLOCK_MARKERS`, size guard, positive `expect=` page check | `constants.py`, `Crawler._is_captcha` / `_looks_like` |
| 0.2b the `expect` check exempts recognised non-book pages (Prime Video, Amazon Music), which are a *result*, not a failure | `constants.NON_BOOK_MARKERS`, `Crawler._is_non_book_page` |
| 0.3 per-ASIN attempt counter -> `unclear_fetch_failed` after 3 | `Store.note_failure`, `search.classify_pending` |
| 0.3b `max_failure_streak`: 3 failures in a row stops the pass instead of flagging the whole backlog | `search.classify_pending` |
| 0.4 one short capped cooldown, then different work; no retry of the same URL | `Crawler._on_block` |
| 0.5 an unreadable search page no longer marks the search `done` | `search._enumerate_page` |
| 1.1 UA taken from the running browser, `HeadlessChrome` removed | `Crawler._detect_ua` |
| 1.2 real Chrome / full Chromium instead of the headless shell | `Crawler._launch` (`browser_channel`) |
| 1.3 cookie jar on disk, reused; no timer-based rotation | `Crawler._save_cookies`, `session_refresh_max: 0` |
| 1.4 `Referer` = the search page the ASIN was found on | `Store.add_pending(referer=)`, `Crawler.goto(referer=)` |
| 1.5 random pop instead of FIFO | `Store.take_pending` |
| 2.1 search-card pre-filter (title/byline/format in the card) | `Crawler.extract_cards` / `_card_wanted` / `discover` |

Verified against the live site at the time of writing:

- the dead ASIN that ended the 2026-11-04 run (`B0HP6MNDX8`) now costs
  **1 navigation, 1 second, 0 block events** and is retired as
  `excluded_dead_asin`;
- `channel="chrome"` gives `plugins=5`, `pdfViewerEnabled=true`,
  `window.chrome` object, `ANGLE Metal Renderer: Apple M4`, UA
  `Chrome/156.0.0.0` — matching the browser's own `userAgentData` (156);
- on a real search-results page the card pre-filter keeps 17 of 22 cards and
  drops 5, all of which are genuinely not Christie books (4 of the 5 were
  *Sponsored* slots);
- a normal product fetch still parses and classifies as before.

Not implemented (still open): 2.2 search-by-ASIN fallback, 2.3 interleaving
searches into the classify phase, 2.4 ISBN-10 side-door, 2.5 deliberate
slowdown.

### Follow-up, 2026-11-05 (same day)

The first version of 0.2 was too strict in the other direction: three Prime
Video pages in a row tripped `max_failure_streak` and ended a run after 19 s
of a 1 h budget. A "film adaptation" is one of the most common things a
Christie book search returns, and `parse_product` already had a branch for it
(`if not title: excluded_not_christie`), which the `expect` gate was firing
before.

Fixed by exempting pages that positively look like content but not a book
(`/gp/video`, `instant-video`, `prime video`) — and only those, so a genuine
layout change still fails loudly rather than silently excluding everything.
23 stale attempt counters from the false positives were cleared and the one
mislabelled record (`B0921KP1PC`, an Amazon Music album) was re-verified into
`excluded_not_christie`.
