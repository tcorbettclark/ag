"""verify-matches: re-checking a match must not destroy the record of it.

The network part is thin (a loop over `goto` + `parse_product`, like `check`);
everything that decides what happens to state.json is tested here.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from christie_hunter.store import Store
from christie_hunter.tools import apply_verification, matches_to_verify


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, asin, cls, **kw):
        self.store.record(asin, classification=cls, title=asin, publisher="P",
                          price="\u00a33.99", evidence=["shot.png"], **kw)


class TestSelection(Base):
    def test_only_matches_by_default(self):
        self.add("MATCH00001", "match")
        self.add("EXCL000001", "excluded_harpercollins")
        self.add("UNCLEAR001", "unclear_publisher")
        self.add("DEAD000001", "excluded_dead_asin")
        self.assertEqual(matches_to_verify(self.store), ["MATCH00001"])

    def test_include_unclear(self):
        self.add("MATCH00001", "match")
        self.add("UNCLEAR001", "unclear_publisher")
        got = matches_to_verify(self.store, include_unclear=True)
        self.assertEqual(got, ["MATCH00001", "UNCLEAR001"])

    def test_since_skips_recently_verified_and_checks_never_verified_first(self):
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        self.add("NEVER00001", "match")
        self.add("RECENT0001", "match", verified_at=datetime.now(timezone.utc).isoformat())
        self.add("OLD0000001", "match", verified_at=old)
        self.assertEqual(matches_to_verify(self.store, since_days=7),
                         ["NEVER00001", "OLD0000001"])
        self.assertEqual(matches_to_verify(self.store, since_days=0),
                         ["NEVER00001", "OLD0000001", "RECENT0001"])


class TestApply(Base):
    def test_a_still_live_match_is_stamped_and_keeps_its_evidence(self):
        self.add("MATCH00001", "match")
        self.store.records["MATCH00001"]["first_seen"] = "2026-01-01T00:00:00+00:00"
        old, new = apply_verification(
            self.store, "MATCH00001", "match",
            {"title": "New title", "publisher": "Classic Harbor", "price": "\u00a34.99"})
        rec = self.store.records["MATCH00001"]
        self.assertEqual((old, new), ("match", "match"))
        self.assertEqual(rec["classification"], "match")
        self.assertEqual(rec["verify_result"], "live")
        self.assertTrue(rec["verified_at"])
        self.assertEqual(rec["title"], "New title")
        self.assertEqual(rec["price"], "\u00a34.99")
        self.assertEqual(rec["evidence"], ["shot.png"])         # untouched
        self.assertEqual(rec["first_seen"], "2026-01-01T00:00:00+00:00")
        self.assertNotIn("previous_classification", rec)

    def test_a_match_that_went_out_of_stock_is_relabelled(self):
        self.add("MATCH00001", "match")
        apply_verification(self.store, "MATCH00001", "excluded_unavailable", {})
        rec = self.store.records["MATCH00001"]
        self.assertEqual(rec["classification"], "excluded_unavailable")
        self.assertEqual(rec["previous_classification"], "match")
        self.assertEqual(rec["verify_result"], "changed")
        self.assertIsNone(rec["price"])          # no stale price left behind
        self.assertEqual(rec["evidence"], ["shot.png"])

    def test_a_match_that_is_gone_from_amazon_is_kept_with_its_evidence(self):
        self.add("MATCH00001", "match")
        old, new = apply_verification(self.store, "MATCH00001",
                                      "excluded_dead_asin", note="status 404")
        rec = self.store.records["MATCH00001"]
        self.assertEqual((old, new), ("match", "excluded_dead_asin"))
        self.assertEqual(rec["verify_result"], "gone")
        self.assertEqual(rec["previous_classification"], "match")
        self.assertEqual(rec["verify_note"], "status 404")
        self.assertEqual(rec["evidence"], ["shot.png"])
        self.assertIn("MATCH00001", self.store.records)     # never deleted

    def test_a_record_that_becomes_harpercollins_moves_out_of_matches(self):
        self.add("MATCH00001", "match")
        apply_verification(self.store, "MATCH00001", "excluded_harpercollins",
                           {"publisher": "HarperCollins"})
        rec = self.store.records["MATCH00001"]
        self.assertEqual(rec["classification"], "excluded_harpercollins")
        self.assertEqual(rec["publisher"], "HarperCollins")
        self.assertEqual(rec["previous_classification"], "match")

    def test_an_unclear_record_that_resolves_becomes_a_match(self):
        self.add("UNCLEAR001", "unclear_publisher")
        apply_verification(self.store, "UNCLEAR001", "match",
                           {"publisher": "Classic Harbor", "price": "\u00a32.99"})
        rec = self.store.records["UNCLEAR001"]
        self.assertEqual(rec["classification"], "match")
        self.assertEqual(rec["verify_result"], "live")
        self.assertEqual(rec["previous_classification"], "unclear_publisher")


if __name__ == "__main__":
    unittest.main()
