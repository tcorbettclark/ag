"""Exceptions that end a run early (all resumable: state.json is saved continuously)."""


class StopRun(Exception):
    """Raised when the run must end (budget, blocks, or Ctrl-C)."""
