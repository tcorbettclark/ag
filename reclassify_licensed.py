#!/usr/bin/env python3
"""Re-classify already-parsed records against `licensed_publishers` (config.json).

Why: the licensed-publisher list grows as rights holders are confirmed. Records
already in state.json were classified against an older list, so a newly
authorised publisher still sits in `match` (i.e. a false positive in
matches.csv) or in `unclear`. This script re-applies the licensed check to the
publisher string we stored, with no network access, then rewrites the reports.

Run this ONLY when no crawler run is active (it rewrites state.json):

    uv run python reclassify_licensed.py
"""

import json
import re
import subprocess
import sys

from christie_hunter import STATE_FILE, CONFIG_FILE, Store, write_reports

if subprocess.run(["pgrep", "-f", "christie_hunter.py"],
                  capture_output=True).returncode == 0:
    sys.exit("REFUSING: a christie_hunter.py run is active. Wait for it to "
             "finish (it saves state continuously and would clobber this change).")

cfg = json.load(open(CONFIG_FILE))
licensed = [re.compile(p, re.I) for p in cfg.get("licensed_publishers", [])]

store = Store(STATE_FILE)
changed = []
for asin, rec in store.records.items():
    cls = rec.get("classification") or ""
    # only records that were NOT already excluded for another reason can change
    if cls and not cls.startswith("unclear") and cls != "match":
        continue
    publisher = rec.get("publisher")
    if not publisher:
        continue
    if any(p.search(publisher) for p in licensed):
        changed.append((asin, cls, publisher))
        rec["classification"] = "excluded_licensed"

if not changed:
    print("No records to re-classify.")
    sys.exit(0)

store.save()
write_reports(store, cfg)
for asin, old, publisher in changed:
    print(f"  {asin}  {old or '(none)':<18} -> excluded_licensed   [{publisher}]")
print(f"\nRe-classified {len(changed)} records as excluded_licensed; reports rewritten.")
