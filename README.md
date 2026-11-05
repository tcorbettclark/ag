# christie-hunter

Finds English-language Agatha Christie books/ebooks on amazon.co.uk that are
**not** published by HarperCollins (any imprint) or by a confirmed licensee —
intended for rights investigation.

## Install and run

```bash
uv sync
uv run playwright install chromium   # first time only

uv run christie-hunter                # a run, 8h by default (config.json)
uv run christie-hunter --for 6h       # or say how long you want
```

`--for` takes `8h`, `90m`, `45s`, `2 hours` — a bare number means hours. A run
ends when either it has run for that long (wall clock) or it has
made `max_navs_per_run` navigations, whichever comes first.

Overnight, detached, with the machine kept awake:

```bash
nohup caffeinate -is uv run christie-hunter --for 8h >> output/console.log 2>&1 &
```

Interrupt anytime with Ctrl-C (progress is saved). Re-run to resume; searches
already completed within the last `search_rescan_days` (config.json) are
skipped — including the author page — and already-classified ASINs are never
re-fetched. A run also stops early by itself when there is nothing left to do.

Other options: `--rescan` (force re-enumeration of every search and the author
page), `--classify-only`, `--search-only`.

## Commands

One entry point, no loose scripts:

| command | what it does |
|---|---|
| `christie-hunter` / `run` | crawl |
| `summary` | classification counts + last run, from state.json |
| `rebuild-reports` | regenerate `output/*.csv` from state.json (no network) |
| `reclassify` | re-apply `licensed_publishers` to existing records |
| `requeue-unavailable` | re-queue `excluded_unavailable` records after a buy-box fix |
| `verify-matches` | re-fetch every match and re-decide it (needs the network) |
| `check <url>` | classify a single ASIN with buy-box forensics |

Everything except `run`, `verify-matches` and `check` is offline and
read-only-ish; all of them refuse to touch state.json while a run is active
(single-writer lock, `output/.run.pid`).

## Outputs

- `output/matches.csv` — the findings: Christie-authored, English, buyable now,
  publisher not a HarperCollins imprint and not a known authorised licensee
  (see `licensed_publishers` in config.json — BBC, Audible, Orion, Mondadori,
  Blackstone, Hachette/Le Masque, Der Hörverlag, …). The `verified_at` and
  `verify_result` columns say whether the listing was still there when it was
  last re-checked (see "Verifying matches")
- `output/unclear.csv` — flagged for human review (contaminated author credit,
  ambiguous publisher such as bare "Harper"/"Avon", missing publisher field, or
  `unclear_fetch_failed` — a page that stayed unreadable after
  `max_asin_attempts` tries)
- `output/all_records.csv` — everything ever classified. `note` explains the
  verdict where the classification alone is terse (`not a book listing`,
  `3 attempts: …`)
- `output/evidence/<ASIN>_<timestamp>.png` + `.html` — timestamped screenshot
  and page HTML for every match/unclear item
- `output/run.log` — full activity log for every command that touches the
  network (`run`, `verify-matches`, `check`), including per-nav timings at
  DEBUG and the browser/UA in use: `nav 42 [classify] load=3.1s wait=8.2s
  html=1317kB <url>`
- `output/.session.json` — the cookie jar, reused between runs and between
  session rotations. Delete it to start from a clean browser.
- `state.json` — ASIN-keyed dedup record and search progress (do not delete;
  it is what makes runs resumable)

## How it searches

1. Author-strict Amazon search (`p_27:Agatha Christie`), both books and
   Kindle indices, paginated
2. Agatha Christie's Amazon author page
3. Per-title searches for every work in `src/christie_hunter/works.py` (66
   novels, Westmacott romances, short story collections, non-fiction — with US
   title variants), both indices

Search-result cards are read in one pass and pre-filtered: a card that is
rendered but never says "Christie" cannot be a Christie book, so it is never
fetched (that was 38% of all product-page navigations). Cards too short to
judge are kept — a wasted fetch is cheaper than a missed match. Set
`prefilter_cards: false` in config.json to turn this off.

Every discovered ASIN is classified in gates: Christie credited →
publisher is not a definite HarperCollins imprint → publisher is not an
authorised licensee → Christie is the primary uncontaminated author →
English language → buyable-new right now. A run alternates the two phases —
enumerate what is stale into the backlog, then classify the backlog — until
the budget or the work runs out.

Runtime is deliberately slow (4–10s random pauses, occasional long pauses). A
full sweep takes multiple nights; state carries the work forward. Measured on
this setup: ~3 browser navigations/min, so an 8h night is roughly 2,300 ASINs.

The browser session and its cookie jar last the whole run and are reused
between runs (`output/.session.json`); a session is replaced only after a
timeout or a block, which is precisely when a new one is worth having. Set
`politeness.session_refresh_max` to a nav count to rotate on a timer instead.

The backlog is not a FIFO queue: `take_pending` pops a random ASIN, so runs do
not walk the same prefix in the same order every night.

## When Amazon says no

A single bad page must never end a run, so navigation failures are per-item:
the item is put back on the queue and the crawl carries on. See
`docs/anti-blocking.md` for the full reasoning.

| what happened | how it is handled |
|---|---|
| 404 / 410 (removed ASIN) | recorded as `excluded_dead_asin`, dropped, **never** treated as a block |
| 200, a real page, but not a book listing (Prime Video, Amazon Music, merch) | recorded as `excluded_not_christie` with the page heading and `note: not a book listing` — a result, not a failure |
| 200 but not the page we asked for, and not recognisably anything | `unexpected page`: retried later, then `unclear_fetch_failed` for review |
| timeout / network error | one more session, retried later |
| 429 / 503 / captcha interstitial | one short cooldown (`cooldown_min_s`–`cooldown_max_s`), fresh session, **different work** — then `max_cooldowns_per_run` of them ends the run |
| `max_failure_streak` failures in a row | the site, not the ASINs: stop the pass instead of flagging the whole backlog |

`max_asin_attempts` (default 3, config.json) is how many unreadable pages one
ASIN may have before it is flagged instead of retried. Note that a 404 page
carries the same "to discuss automated access…" boilerplate as a captcha; the
crawler deliberately does not match on it (that turned one dead ASIN into 65
minutes of cooldowns and an aborted 14h run).

## Verifying matches

A `match` is a claim about the state of a listing on the day it was fetched.
Self-published Kindle editions in particular disappear: of the first three
matches re-checked after this command was added, one had already gone.

```bash
uv run christie-hunter verify-matches                 # every match
uv run christie-hunter verify-matches --since 30      # only those not checked in 30 days
uv run christie-hunter verify-matches --include-unclear
uv run christie-hunter verify-matches --asin B0HNTNNCHG --asin B0HMJT8V14
uv run christie-hunter verify-matches --limit 20 --for 20m
```

Each record is re-fetched and re-decided by exactly the same pipeline as a run,
and the verdict becomes its `classification` — so `matches.csv` always means
"a match right now", never "was a match in November". Nothing is deleted and
nothing is rewritten silently:

| field | meaning |
|---|---|
| `verified_at` | when it was last re-checked |
| `verify_result` | `live`, `gone`, `changed`, or absent (never verified) |
| `previous_classification` | what it was before the verdict changed |
| `verify_note` | why it could not be checked, if it could not (`status 404`, …) |
| `evidence` | **never touched** — for a listing that has gone, the old screenshot is the only proof left |

An item that is gone becomes `excluded_dead_asin`; one that has gone out of
stock becomes `excluded_unavailable` (and `requeue-unavailable` can bring it
back later); one that has since been republished by HarperCollins moves to
`excluded_harpercollins`. Records that could not be fetched at all are left
exactly as they were and reported separately, so a bad night on Amazon never
quietly empties `matches.csv`.

## Confirmed licensees

`licensed_publishers` in config.json is a list of case-insensitive regexes
matched against the publisher string. Anything matching is filed as
`excluded_licensed` (authorised edition — not a rights problem) rather than
`match`. Extend the list whenever a licensee is confirmed; no code change and
no re-crawl needed:

```bash
uv run christie-hunter reclassify   # re-apply the list to state.json + reports
```

`reclassify` re-decides every record except the two gates that run *before* the
licensed check (`excluded_harpercollins`, `excluded_not_christie`) — so a
licensed French edition already filed as `excluded_not_english` is relabelled
`excluded_licensed`, matching the pipeline's order.

## What to back up

`state.json` is the whole logical state and the only file worth backing up for
continuity. It holds:

- `records` — every classified ASIN with all report fields (`classification`,
  `title`, `publisher`, `language`, `price`, `evidence` paths, timestamps)
- `pending` — the backlog: ASINs discovered but never fetched
- `in_flight` — anything mid-fetch when the previous run died (re-queued on load)
- `search_progress` — per-search `next_page`/`done`, which is what makes
  completed searches skippable for `search_rescan_days`
- `sources` — ASIN → the search that found it (superset of `records ∪ pending`)
- `runs` — one summary per run (start, end, budget, navs, end_reason)

Nothing else is needed to regenerate the reports — `output/*.csv` are pure
derived views, and `uv run christie-hunter rebuild-reports` rebuilds them from
`state.json` with no network access.

Back up two things, not one:

| what | why |
|---|---|
| `state.json` | the logic: findings, backlog, search progress, verification history. Expensive to re-derive (rate-limited crawl) |
| `output/evidence/` | the proof: timestamped PNG + HTML for every `match`/`unclear`. The record only stores the path; without the file it cannot be reproduced except by re-fetching the page |

`config.json` and the works list determine what gets searched and how it is
classified, but both are in git. `output/run.log`, `console.log` and the CSVs
are history/derived — safe to lose. `state.json` is written atomically (temp
file + rename), so a killed run cannot leave it half-written.

## Development

```bash
uv run python -m unittest discover -s tests -t . -v
```

Layout: `src/christie_hunter/` — `budget.py` (duration/deadline maths),
`crawler.py` (Playwright + classification pipeline), `search.py` (the two
phases), `store.py` (state.json + writer lock), `reports.py`, `tools.py`
(maintenance commands), `cli.py`, `works.py` (the canon).

Known loose ends:

- the buy-box check (`_buyability`) is heuristic and was written against the
  late-2026 Kindle layout; `requeue-unavailable` exists for when it misfires
- the search-result pre-filter trusts card text; a card whose title renders as
  an image only would be dropped. `prefilter_cards: false` reverts to fetching
  every ASIN on the page
- non-English editions from Audible-family labels sometimes carry
  `Language: English` on the page, so the language gate can miss them —
  licensed publishers are excluded before that gate, which covers most cases
