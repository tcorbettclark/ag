"""Persistent, ASIN-keyed state: everything the crawler remembers between runs.

state.json is the single source of truth and is written atomically (temp file +
rename), so a killed run cannot leave it half-written.
"""

import json
import os
import random
from pathlib import Path

from .constants import STATE_FILE, now_iso


class Store:
    """Persistent, ASIN-keyed record of everything we've seen."""

    def __init__(self, path: Path = STATE_FILE):
        self.path = Path(path)
        if self.path.exists():
            with open(self.path) as f:
                self.data = json.load(f)
        else:
            self.data = {"records": {}, "pending": [], "in_flight": [],
                         "search_progress": {}, "runs": []}
        for key, default in (("records", {}), ("pending", []),
                             ("in_flight", []), ("search_progress", {}),
                             ("runs", []), ("sources", {}),
                             ("referers", {}), ("attempts", {})):
            self.data.setdefault(key, default)
        # anything mid-fetch when a previous run died goes back in the queue
        for asin in self.data["in_flight"]:
            if asin not in self.data["pending"]:
                self.data["pending"].append(asin)
        self.data["in_flight"] = []

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        tmp.replace(self.path)

    # --- helpers ---
    @property
    def records(self):
        return self.data["records"]

    @property
    def pending(self):
        return self.data["pending"]

    def known(self, asin):
        return asin in self.data["records"] or asin in self.data["pending"]

    def add_pending(self, asins, source, referer=None):
        added = 0
        for a in asins:
            if not self.known(a):
                self.data["pending"].append(a)
                # remember where it came from (used when classified)
                self.data["sources"][a] = source
                if referer:
                    # the exact page a human would have clicked through from
                    self.data["referers"][a] = referer
                added += 1
        return added

    def take_pending(self):
        """Pop a random ASIN.

        Deliberately not FIFO: a run that always walks the same prefix of the
        queue produces the same long unbroken sequence of /dp/ hits every night.
        """
        pending = self.data["pending"]
        if not pending:
            return None
        asin = pending.pop(random.randrange(len(pending)))
        self.data["in_flight"].append(asin)
        self.save()
        return asin

    def requeue(self, asin):
        """Put an ASIN we could not read back in the queue, at the far end."""
        if asin in self.data["in_flight"]:
            self.data["in_flight"].remove(asin)
        if asin not in self.data["pending"] and asin not in self.records:
            self.data["pending"].append(asin)
        self.save()

    def note_failure(self, asin, reason):
        """Count one failed attempt on an ASIN. Returns the running total."""
        entry = self.data["attempts"].setdefault(asin, {"count": 0})
        entry["count"] += 1
        entry["reason"] = reason
        entry["at"] = now_iso()
        return entry["count"]

    def clear_failures(self, asin):
        self.data["attempts"].pop(asin, None)

    def referer(self, asin):
        return self.data["referers"].get(asin)

    def record(self, asin, **fields):
        rec = self.data["records"].setdefault(asin, {"first_seen": now_iso()})
        rec.update(fields)
        rec["last_seen"] = now_iso()
        if asin in self.data["in_flight"]:
            self.data["in_flight"].remove(asin)
        self.data["attempts"].pop(asin, None)
        self.save()

    def progress(self, key):
        return self.data["search_progress"].setdefault(
            key, {"next_page": 1, "done": False, "last_enumerated": None})


# --- single-writer lock -------------------------------------------------------

def lock_path(cfg):
    return Path(cfg["output_dir"]) / ".run.pid"


def active_run_pid(cfg):
    """PID of a live crawler run, or None. Stale pid files are removed."""
    path = lock_path(cfg)
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        path.unlink(missing_ok=True)
        return None
    return pid


def require_idle(cfg):
    """Exit if a crawler run is active (state.json must not have two writers)."""
    pid = active_run_pid(cfg)
    if pid:
        raise SystemExit(
            f"REFUSING: a run is active (pid {pid}). Wait for it to finish — it "
            "saves state continuously and would clobber this change.")
    return True


def write_lock(cfg):
    lock_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    lock_path(cfg).write_text(str(os.getpid()))


def clear_lock(cfg):
    lock_path(cfg).unlink(missing_ok=True)
