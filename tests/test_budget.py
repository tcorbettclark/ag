"""Run budget: duration parsing and the time/nav ceilings."""

import unittest
from datetime import datetime, timedelta, timezone

from christie_hunter.budget import RunBudget, parse_duration

NOW = datetime(2026, 11, 4, 22, 0, tzinfo=timezone.utc)


class TestParseDuration(unittest.TestCase):
    def test_units(self):
        self.assertEqual(parse_duration("8h"), 8.0)
        self.assertEqual(parse_duration("90m"), 1.5)
        self.assertEqual(parse_duration("45s"), 45 / 3600)
        self.assertEqual(parse_duration("1.5h"), 1.5)

    def test_bare_number_is_hours(self):
        self.assertEqual(parse_duration("2"), 2.0)
        self.assertEqual(parse_duration(8), 8.0)
        self.assertEqual(parse_duration(0.5), 0.5)

    def test_spelled_out(self):
        self.assertEqual(parse_duration("2 hours"), 2.0)
        self.assertEqual(parse_duration("30 min"), 0.5)
        self.assertEqual(parse_duration("15sec"), 15 / 3600)

    def test_rejects_nonsense(self):
        for bad in ("", "h", "abc", "8x", "0", "-1h", "0h"):
            with self.assertRaises(ValueError, msg=bad):
                parse_duration(bad)


class TestRunBudget(unittest.TestCase):
    def budget(self, hours=8, max_navs=2500):
        return RunBudget.start(hours, max_navs, now=NOW)

    def test_deadline_is_wall_clock(self):
        b = self.budget()
        self.assertEqual(b.deadline, NOW + timedelta(hours=8))

    def test_running_out_of_time(self):
        b = self.budget()
        self.assertFalse(b.time_up(NOW + timedelta(hours=7, minutes=59)))
        self.assertTrue(b.time_up(NOW + timedelta(hours=8)))
        self.assertTrue(b.time_up(NOW + timedelta(hours=9)))
        self.assertEqual(b.remaining_h(NOW + timedelta(hours=6)), 2.0)
        self.assertEqual(b.remaining_h(NOW + timedelta(hours=20)), 0.0)

    def test_sleeping_counts_as_elapsed(self):
        # wall clock, not active time: a machine that slept still burns budget
        b = self.budget(hours=8)
        self.assertTrue(b.time_up(NOW + timedelta(hours=10)))

    def test_running_out_of_navs(self):
        b = self.budget(max_navs=100)
        self.assertFalse(b.exhausted(99, NOW))
        self.assertTrue(b.exhausted(100, NOW))
        self.assertEqual(b.navs_left(40), 60)
        self.assertEqual(b.navs_left(400), 0)

    def test_why_exhausted_is_specific(self):
        b = self.budget(max_navs=100)
        self.assertIn("navigation ceiling", b.why_exhausted(100, NOW))
        self.assertIn("budget spent", b.why_exhausted(5, NOW + timedelta(hours=9)))
        self.assertEqual(b.why_exhausted(5, NOW), "")

    def test_scaled_sub_budget_shares_the_start(self):
        b = self.budget()
        sub = b.scaled(0.6)
        self.assertEqual(sub.started, b.started)
        self.assertAlmostEqual(sub.hours, 4.8)
        self.assertEqual(sub.max_navs, 1500)
        self.assertAlmostEqual(sub.deadline, NOW + timedelta(hours=4.8))

    def test_scaled_never_goes_below_one_nav(self):
        self.assertEqual(self.budget(max_navs=1).scaled(0.1).max_navs, 1)

    def test_describe_states_both_numbers(self):
        text = self.budget().describe()
        self.assertIn("8h", text)
        self.assertIn("2,500", text)


if __name__ == "__main__":
    unittest.main()
