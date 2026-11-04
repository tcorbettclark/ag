#!/usr/bin/env python3
"""Re-queue records classified `excluded_unavailable` for re-classification.

Why: the 2026-11-03 overnight run used a _buyability() that missed Amazon's
new Kindle buy-box layout (input[value="Buy now"]), so genuinely-buyable
Kindle editions could be misfiled as excluded_unavailable. After the fix,
this script puts those records back in the pending queue so the next run
re-classifies them with the corrected logic.

Run this ONLY when no crawler run is active (it rewrites state.json):

    ps -p <pid>          # check the overnight run has finished
    uv run python requeue_unavailable.py
"""

import json
import subprocess
import sys

if subprocess.run(["pgrep", "-f", "christie_hunter.py"],
                 capture_output=True).returncode == 0:
    sys.exit("REFUSING: a christie_hunter.py run is active. Wait for it to "
             "finish (it saves state continuously and would clobber this change).")

s = json.load(open("state.json"))
records = s["records"]
targets = [a for a, r in records.items()
           if r.get("classification") == "excluded_unavailable"]
if not targets:
    print("No excluded_unavailable records to re-queue.")
    sys.exit(0)

# drop the stale records, push the ASINs back to the pending queue
for a in targets:
    del records[a]
    if a not in s["pending"]:
        s["pending"].insert(0, a)
    s.get("sources", {})[a] = "requeued_unavailable"

json.dump(s, open("state.json", "w"), indent=2, ensure_ascii=False)
print(f"Re-queued {len(targets)} ASINs: {targets}")
print("They will be re-classified on the next run (with the fixed _buyability).")