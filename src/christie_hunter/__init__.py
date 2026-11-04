"""christie-hunter — find English-language Agatha Christie books/ebooks on
amazon.co.uk that are NOT published by HarperCollins or a known licensee.

Resumable: all progress lives in state.json (ASIN-keyed). Interrupt anytime
with Ctrl-C; the next run picks up exactly where this one left off.

A run is bounded by wall clock — `--for 8h` or `run_hours` in config.json — and
by a hard navigation ceiling, whichever comes first.
"""

__version__ = "0.2.0"

from .crawler import Crawler                     # noqa: F401
from .budget import RunBudget, parse_duration     # noqa: F401
from .store import Store                          # noqa: F401
from .reports import write_reports                # noqa: F401

__all__ = ["Crawler", "RunBudget", "Store", "parse_duration", "write_reports",
           "__version__"]
