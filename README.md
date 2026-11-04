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
  (licensed publishers like BBC and Laurence King are configurable in
  config.json `licensed_publishers`)
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