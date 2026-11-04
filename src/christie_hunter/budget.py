"""The one number that decides how long a run lasts.

A run is bounded by wall-clock hours *or* a navigation ceiling, whichever comes
first. Wall clock (not CPU/monotonic) is deliberate: a run started at 22:00 for
8h is finished by breakfast even if the machine slept in between.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_UNIT_HOURS = {"s": 1 / 3600, "m": 1 / 60, "h": 1.0}
_SUFFIXES = (("secs", "s"), ("sec", "s"), ("minutes", "m"), ("mins", "m"),
             ("min", "m"), ("hours", "h"), ("hour", "h"), ("hrs", "h"), ("hr", "h"))


def parse_duration(text) -> float:
    """Parse '8h', '90m', '45s', '1.5h' (or a bare number = hours) into hours."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        hours = float(text)
    else:
        t = str(text).strip().lower()
        for long, short in _SUFFIXES:
            if t.endswith(long):
                t = t.replace(long, short)
                break
        unit = "h"  # bare number means hours
        if t and t[-1] in _UNIT_HOURS:
            unit, t = t[-1], t[:-1]
        try:
            hours = float(t) * _UNIT_HOURS[unit]
        except ValueError:
            raise ValueError(
                f"cannot read duration {text!r} — use e.g. 8h, 90m, 45s") from None
    if hours <= 0:
        raise ValueError("duration must be positive")
    return hours


def format_hours(hours: float) -> str:
    """8.0 -> '8h', 0.5 -> '30m', 0.025 -> '1.5m', 0.004 -> '15s'."""
    if hours >= 1:
        return f"{hours:g}h"
    minutes = hours * 60
    if minutes >= 1:
        return f"{round(minutes, 1):g}m"
    return f"{round(minutes * 60):g}s"


@dataclass(frozen=True)
class RunBudget:
    """A wall-clock deadline plus a hard navigation ceiling."""

    hours: float
    max_navs: int
    started: datetime

    @classmethod
    def start(cls, hours, max_navs, now=None):
        return cls(float(hours), int(max_navs), now or datetime.now(timezone.utc))

    # -- time --
    @property
    def deadline(self):
        return self.started + timedelta(hours=self.hours)

    def elapsed_h(self, now=None):
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - self.started).total_seconds() / 3600)

    def remaining_h(self, now=None):
        return max(0.0, self.hours - self.elapsed_h(now))

    def time_up(self, now=None):
        return self.elapsed_h(now) >= self.hours

    # -- navs --
    def navs_left(self, navs):
        return max(0, self.max_navs - navs)

    def exhausted(self, navs, now=None):
        return self.time_up(now) or navs >= self.max_navs

    def why_exhausted(self, navs, now=None):
        if navs >= self.max_navs:
            return f"navigation ceiling reached ({self.max_navs})"
        if self.time_up(now):
            return f"run budget spent ({format_hours(self.hours)})"
        return ""

    # -- derived budgets --
    def scaled(self, fraction):
        """A sub-budget: same start, `fraction` of the hours and the navs."""
        return RunBudget(self.hours * fraction,
                         max(1, int(self.max_navs * fraction)),
                         self.started)

    def describe(self, now=None):
        ends = self.deadline.astimezone().strftime("%H:%M")
        return (f"{format_hours(self.hours)} (ends {ends}) or "
                f"{self.max_navs:,} navigations")
