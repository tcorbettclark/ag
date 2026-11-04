# christie-hunter

Finds English-language Agatha Christie books/ebooks on amazon.co.uk that are
**not** published by HarperCollins (any imprint) — intended for rights
investigation.

## Run

```bash
uv sync
uv run playwright install chromium   # first time only
uv run python christie_hunter.py
```

Interrupt anytime with Ctrl-C (progress is saved). Re-run to resume; searches
already completed within the last `search_rescan_days` (config.json) are
skipped, already-classified ASINs are never re-fetched.

Options: `--limit N` (cap navigations), `--rescan` (force re-enumeration of
all searches), `--classify-only`, `--search-only`.

## Outputs

- `output/matches.csv` — the findings: Christie-authored, English, buyable now,
  publisher not a HarperCollins imprint and not a known authorised licensee
  (see `licensed_publishers` in config.json — BBC, Audible, Orion, Mondadori,
  Blackstone, Der Hörverlag, …)
- `output/unclear.csv` — flagged for human review (contaminated author credit,
  ambiguous publisher such as bare "Harper"/"Avon", missing publisher field)
- `output/all_records.csv` — everything ever classified
- `output/evidence/<ASIN>_<timestamp>.png` + `.html` — timestamped screenshot
  and page HTML for every match/unclear item
- `output/run.log` — full activity log
- `state.json` — ASIN-keyed dedup record and search progress (do not delete;
  it is what makes runs resumable)

## How it searches

1. Author-strict Amazon search (`p_27:Agatha Christie`), both books and
   Kindle indices, paginated
2. Agatha Christie's Amazon author page
3. Per-title searches for every work in `christie_works.py` (66 novels,
   Westmacott romances, short story collections, non-fiction — with US title
   variants), both indices

Every discovered ASIN is classified in gates: Christie credited →
publisher is not a definite HarperCollins imprint → publisher is not an
authorised licensee → Christie is the primary uncontaminated author →
English language → buyable-new right now. 60% of each run's navigation
budget goes to search, 40% to classification, so every run progresses both.

Runtime is deliberately slow (4–10s random pauses, occasional long pauses,
fresh browser session every ~20–35 navigations, hard navigation budget per
run). A full sweep takes multiple overnight runs; state carries the work
forward.

## Confirmed licensees

`licensed_publishers` in config.json is a list of case-insensitive regexes
matched against the publisher string. Anything matching is filed as
`excluded_licensed` (authorised edition — not a rights problem) rather than
`match`. Extend the list whenever a licensee is confirmed; no code change and
no re-crawl needed:

```bash
uv run python reclassify_licensed.py   # re-apply the list to state.json + reports
```

`reclassify_licensed.py` regenerates the CSVs without any network access. It
re-decides every record except the two gates that run *before* the licensed
check (`excluded_harpercollins`, `excluded_not_christie`) — so a licensed
French edition already filed as `excluded_not_english` is relabelled
`excluded_licensed`, matching the pipeline's order. Like
`requeue_unavailable.py`, it refuses to run while the crawler is active.

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
- `runs` — one summary per run (start, end, navs, end_reason)

Nothing else is needed to regenerate the reports — `output/*.csv` are pure
derived views, and `uv run python rebuild_reports.py` rebuilds them from
`state.json` with no network access (byte-identical, verified).

Back up two things, not one:

| what | why |
|---|---|
| `state.json` | the logic: findings, backlog, search progress. Expensive to re-derive (rate-limited crawl) |
| `output/evidence/` | the proof: timestamped PNG + HTML for every `match`/`unclear`. The record only stores the path; without the file it cannot be reproduced except by re-fetching the page |

`config.json` and `christie_works.py` determine what gets searched and how it
is classified, but both are in git. `output/run.log`, `console.log` and the
CSVs are history/derived — safe to lose. `state.json` is written atomically
(temp file + rename), so a killed run cannot leave it half-written.
