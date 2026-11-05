"""Exceptions raised during navigation.

`StopRun` ends the run. The others are *per-page* outcomes that the caller
handles and carries on from — the distinction matters: a dead ASIN or one
unreadable page must never be able to end a run.
"""


class StopRun(Exception):
    """Raised when the run must end (budget, real block, or Ctrl-C)."""


class PageFailure(Exception):
    """Base for a single page that did not work out. Never ends the run."""


class DeadPage(PageFailure):
    """404/410: the ASIN is gone. Not a block, not a retry, just dead."""


class Transient(PageFailure):
    """Timeout or network hiccup. Worth one more attempt on a later pass."""


class Blocked(PageFailure):
    """A block was seen; we cooled down and started a fresh session.

    The caller puts the item back in the queue and continues with different
    work, rather than hammering the same URL (which is what made the block
    worse in the first place).
    """


class UnexpectedPage(PageFailure):
    """We got a 200, but not the kind of page we asked for.

    Either a soft block or an Amazon layout change. Treated as transient: retry
    later, and after `max_asin_attempts` flag it for a human instead of
    silently believing it.
    """
