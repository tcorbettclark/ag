"""config.json loading, with the defaults the code assumes if a key is absent."""

import json
from pathlib import Path

from .constants import CONFIG_FILE

DEFAULTS = {
    # how long a run lasts, wall clock (a bare `christie-hunter` uses this)
    "run_hours": 8,
    # hard ceiling, whatever the clock says
    "max_navs_per_run": 2500,
    # share of the budget the enumeration phase may use before classifying
    "search_share": 0.6,
    "output_dir": "output",
    # "chrome" / "chromium" / None to force one; "auto" = try real Chrome first
    "browser_channel": "auto",
    # give up on one ASIN after this many unreadable pages, flag it for review
    "max_asin_attempts": 3,
    # this many failures in a row means the site, not the ASINs: stop the pass
    "max_failure_streak": 3,
    # drop search-result cards that clearly are not Christie books, pre-fetch
    "prefilter_cards": True,
}


def load(path=None):
    cfg = json.loads(Path(path or CONFIG_FILE).read_text())
    for key, value in DEFAULTS.items():
        cfg.setdefault(key, value)
    # politeness.max_navs_per_run is obsolete (it is a run budget, not politeness)
    cfg.get("politeness", {}).pop("max_navs_per_run", None)
    return cfg
