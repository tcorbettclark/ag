#!/usr/bin/env python3
"""Regenerate output/*.csv from state.json alone - no network, no state change.

state.json is the single source of truth: every classified ASIN (with the
fields the reports need) plus the pending backlog, search progress and run
history. This script proves it, and is the recovery path after restoring a
state.json backup:

    uv run python rebuild_reports.py

Read-only, so unlike reclassify_licensed.py / requeue_unavailable.py it is
safe to run while the crawler is active (the crawler will rewrite the same
CSVs at the end of its run anyway).
"""

import json

from christie_hunter import CONFIG_FILE, STATE_FILE, Store, write_reports

cfg = json.load(open(CONFIG_FILE))
store = Store(STATE_FILE)  # in_flight -> pending happens in memory only; nothing is saved
n = len(store.records)
write_reports(store, cfg)
print(f"rebuilt reports from {STATE_FILE} ({n} classified ASINs, "
      f"{len(store.data['pending'])} pending) into {cfg['output_dir']}/")
