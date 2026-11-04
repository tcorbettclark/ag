"""Store: queue handling, atomic saves, and the single-writer lock."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from christie_hunter.store import (Store, active_run_pid, clear_lock,
                                   require_idle, write_lock)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_state_is_empty(self):
        s = Store(self.path)
        self.assertEqual(s.records, {})
        self.assertEqual(s.pending, [])
        self.assertFalse(self.path.exists())   # nothing written until asked

    def test_round_trip(self):
        s = Store(self.path)
        s.add_pending(["B000000001"], "generic|stripbooks")
        s.record("B000000001", classification="match", title="X")
        again = Store(self.path)
        self.assertEqual(again.records["B000000001"]["classification"], "match")
        self.assertEqual(again.data["sources"]["B000000001"], "generic|stripbooks")

    def test_interrupted_fetch_returns_to_the_queue(self):
        data = {"records": {}, "pending": ["A"], "in_flight": ["B"],
                "search_progress": {}, "runs": []}
        self.path.write_text(json.dumps(data))
        s = Store(self.path)
        self.assertEqual(s.pending, ["A", "B"])
        self.assertEqual(s.data["in_flight"], [])

    def test_add_pending_deduplicates(self):
        s = Store(self.path)
        s.add_pending(["A", "B", "A"], "src")
        s.add_pending(["B", "C"], "src")
        self.assertEqual(s.pending, ["A", "B", "C"])

    def test_take_pending_is_fifo_and_tracked(self):
        s = Store(self.path)
        s.add_pending(["A", "B"], "src")
        self.assertEqual(s.take_pending(), "A")
        self.assertEqual(s.data["in_flight"], ["A"])
        s.record("A", classification="match")
        self.assertEqual(s.data["in_flight"], [])
        self.assertEqual(s.pending, ["B"])
        s.take_pending()
        self.assertIsNone(s.take_pending())

    def test_save_leaves_no_temp_file(self):
        s = Store(self.path)
        s.record("A", classification="match")
        leftovers = [p.name for p in Path(self.tmp.name).iterdir()]
        self.assertEqual(leftovers, ["state.json"])

    def test_progress_defaults(self):
        s = Store(self.path)
        p = s.progress("authorpage")
        self.assertEqual(p, {"next_page": 1, "done": False, "last_enumerated": None})


class TestLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = {"output_dir": self.tmp.name}

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_lock_when_idle(self):
        self.assertIsNone(active_run_pid(self.cfg))
        self.assertTrue(require_idle(self.cfg))

    def test_live_pid_blocks(self):
        (Path(self.tmp.name) / ".run.pid").write_text(str(os.getpid()))
        self.assertEqual(active_run_pid(self.cfg), os.getpid())
        with self.assertRaises(SystemExit):
            require_idle(self.cfg)

    def test_stale_pid_is_cleaned_up(self):
        path = Path(self.tmp.name) / ".run.pid"
        path.write_text("999999")            # not a live pid
        self.assertIsNone(active_run_pid(self.cfg))
        self.assertFalse(path.exists())
        self.assertTrue(require_idle(self.cfg))

    def test_garbage_lock_is_ignored(self):
        (Path(self.tmp.name) / ".run.pid").write_text("not-a-pid")
        self.assertIsNone(active_run_pid(self.cfg))

    def test_write_and_clear(self):
        write_lock(self.cfg)
        self.assertEqual(active_run_pid(self.cfg), os.getpid())
        clear_lock(self.cfg)
        self.assertIsNone(active_run_pid(self.cfg))


if __name__ == "__main__":
    unittest.main()
