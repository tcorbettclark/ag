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
| `check <url>` | classify a single ASIN with buy-box forensics |

Everything except `run` and `check` is offline and read-only-ish; all of them
refuse to touch state.json while a run is active (single-writer lock,
`output/.run.pid`).

## Outputs

- `output/matches.csv` — the findings: Christie-authored, English, buyable now,
  publisher not a HarperCollins imprint and not a known authorised licensee
  (see `licensed_publishers` in config.json — BBC, Audible, Orion, Mondadori,
  Blackstone, Hachette/Le Masque, Der Hörverlag, …)
- `output/unclear.csv` — flagged for human review (contaminated author credit,
  ambiguous publisher such as bare "Harper"/"Avon", missing publisher field)
- `output/all_records.csv` — everything ever classified
- `output/evidence/<ASIN>_<timestamp>.png` + `.html` — timestamped screenshot
  and page HTML for every match/unclear item
- `output/run.log` — full activity log, including per-nav timings at DEBUG
  (`nav 42 [classify] load=3.1s wait=8.2s html=1317kB <url>`)
- `state.json` — ASIN-keyed dedup record and search progress (do not delete;
  it is what makes runs resumable)

## How it searches

1. Author-strict Amazon search (`p_27:Agatha Christie`), both books and
   Kindle indices, paginated
2. Agatha Christie's Amazon author page
3. Per-title searches for every work in `src/christie_hunter/works.py` (66
   novels, Westmacott romances, short story collections, non-fiction — with US
   title variants), both indices

Every discovered ASIN is classified in gates: Christie credited →
publisher is not a definite HarperCollins imprint → publisher is not an
authorised licensee → Christie is the primary uncontaminated author →
English language → buyable-new right now. A run alternates the two phases —
enumerate what is stale into the backlog, then classify the backlog — until
the budget or the work runs out.

Runtime is deliberately slow (4–10s random pauses, occasional long pauses,
fresh browser session every ~20–35 navigations). A full sweep takes multiple
nights; state carries the work forward. Measured on this setup: ~3 browser
navigations/min, so an 8h night is roughly 2,300 ASINs.

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
| `state.json` | the logic: findings, backlog, search progress. Expensive to re-derive (rate-limited crawl) |
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
- non-English editions from Audible-family labels sometimes carry
  `Language: English` on the page, so the language gate can miss them —
  licensed publishers are excluded before that gate, which covers most cases
