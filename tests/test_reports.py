"""Reports are pure derived data: same records in, same CSVs out.

The golden test pins the *shape* (columns, ordering, evidence joining) so the
package refactor could not silently change the files a human reads.
"""

import csv
import tempfile
import unittest
from pathlib import Path

from christie_hunter.reports import COLUMNS, write_reports


class FakeStore:
    def __init__(self, records):
        self.records = records


RECORDS = {
    "B000000002": {"classification": "excluded_not_english", "title": "Dix petits nègres",
                   "publisher": "Le Masque", "language": "French",
                   "first_seen": "2026-11-04T00:00:00+00:00", "url": "u2"},
    "B000000001": {"classification": "match", "title": "The Big Four",
                   "publisher": "Digital Ink", "language": "English", "price": "£1.99",
                   "evidence": ["output/evidence/a.png", "output/evidence/a.html"],
                   "first_seen": "2026-11-04T00:00:00+00:00", "url": "u1"},
    "B000000003": {"classification": "unclear_publisher", "title": "Five Little Pigs",
                   "publisher": "Harper", "first_seen": "2026-11-04T00:00:00+00:00",
                   "url": "u3"},
    "B000000004": {"classification": "excluded_harpercollins", "title": "Hickory Dickory Dock",
                   "first_seen": "2026-11-04T00:00:00+00:00", "url": "u4"},
}


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


class TestWriteReports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = {"output_dir": self.tmp.name}
        write_reports(FakeStore(RECORDS), self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def out(self, name):
        return Path(self.tmp.name) / name

    def test_columns_are_stable(self):
        fields, _ = read(self.out("all_records.csv"))
        self.assertEqual(fields, COLUMNS)
        self.assertEqual(fields[:4], ["asin", "classification", "verified_at",
                                      "verify_result"])

    def test_files_are_split_by_classification(self):
        self.assertEqual([r["asin"] for r in read(self.out("matches.csv"))[1]],
                         ["B000000001"])
        self.assertEqual([r["asin"] for r in read(self.out("unclear.csv"))[1]],
                         ["B000000003"])
        self.assertEqual(sorted(r["asin"] for r in read(self.out("all_records.csv"))[1]),
                         sorted(RECORDS))          # all_records keeps everything

    def test_evidence_list_is_joined(self):
        row = read(self.out("matches.csv"))[1][0]
        self.assertEqual(row["evidence"],
                         "output/evidence/a.png;output/evidence/a.html")

    def test_missing_fields_become_empty_strings(self):
        row = {r["asin"]: r for r in read(self.out("all_records.csv"))[1]}["B000000004"]
        self.assertEqual(row["publisher"], "")
        self.assertEqual(row["evidence"], "")

    def test_absolute_paths_do_not_leak_into_bundles(self):
        # evidence paths are stored relative so state.json survives a move
        for row in read(self.out("matches.csv"))[1]:
            self.assertFalse(row["evidence"].startswith("/"))


class TestColumnDrift(unittest.TestCase):
    def test_every_record_field_used_by_reports_is_a_column(self):
        for rec in RECORDS.values():
            for key in rec:
                self.assertIn(key, COLUMNS, f"{key} would be dropped from the CSV")


if __name__ == "__main__":
    unittest.main()
