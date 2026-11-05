"""The review workbook: routing, presentation, and the living-document notes.

`build_workbook` is pure, so most of this needs no disk and no state.json.
"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from christie_hunter.excel import (build_workbook, clean_author, export,
                                   price_value, primary_author, read_notes, route)

RECORDS = {
    "B000000001": {
        "classification": "match", "title": "The Big Four",
        "author_line": "by Agatha Christie (Author)  Format: Kindle Edition",
        "publisher": "Digital Ink", "format": "Kindle Edition",
        "price": "£2.99", "url": "https://www.amazon.co.uk/dp/B000000001",
        "last_seen": "2026-11-04T10:00:00+00:00",
        "verified_at": "2026-11-05T09:00:00+00:00",
        "evidence": ["output/evidence/B000000001_20261104_100000.png"],
    },
    "B000000002": {
        "classification": "match", "title": "Partners in Crime",
        "author_line": "by Agatha Christie, Hugh Fraser (Narrator)  Format: Audio Cassette",
        "publisher": "Audio Partners", "format": "Audio Cassette",
        "price": "£72.28", "url": "https://www.amazon.co.uk/dp/B000000002",
        "last_seen": "2026-11-04T10:05:00+00:00",
    },
    "B000000003": {
        "classification": "unclear_author", "title": "Der offene Sarg",
        "author_line": "German edition by Sophie Hannah als Agatha Christie",
        "publisher": "dotbooks", "format": "Kindle Edition",
        "url": "https://www.amazon.co.uk/dp/B000000003",
        "last_seen": "2026-11-04T10:10:00+00:00",
    },
    "B000000004": {
        "classification": "unclear_parse_fail", "title": "",
        "author_line": "", "publisher": None, "format": None,
        "url": "https://www.amazon.co.uk/dp/B000000004",
        "last_seen": "2026-11-04T10:15:00+00:00",
    },
    "B000000005": {
        "classification": "unclear_no_publisher", "title": "A.B.C. Murders",
        "author_line": "by Agatha Christie", "publisher": None,
        "format": "Audio CD", "url": "https://www.amazon.co.uk/dp/B000000005",
        "last_seen": "2026-11-04T10:20:00+00:00",
    },
    "B000000006": {
        "classification": "excluded_dead_asin",
        "previous_classification": "match", "title": "Gone Book",
        "author_line": "by Agatha Christie", "publisher": "Truesign Publisher",
        "format": "Kindle Edition", "url": "https://www.amazon.co.uk/dp/B000000006",
        "last_seen": "2026-11-04T10:25:00+00:00",
        "verified_at": "2026-11-05T11:00:00+00:00",
        "evidence": ["output/evidence/B000000006_20261104_102500.png"],
    },
    "B000000007": {
        "classification": "excluded_unavailable",
        "previous_classification": "match", "title": "Out of Stock",
        "author_line": "by Agatha Christie", "publisher": "Heron Books",
        "format": "Hardcover", "url": "https://www.amazon.co.uk/dp/B000000007",
        "verified_at": "2026-11-05T11:05:00+00:00",
    },
    "B000000008": {
        "classification": "excluded_harpercollins", "title": "Not a finding",
        "publisher": "HarperCollins", "format": "Paperback",
    },
}


class TestValues(unittest.TestCase):
    def test_author_line_is_cleaned(self):
        self.assertEqual(
            clean_author("by Agatha Christie (Author)  Format: Kindle Edition"),
            "Agatha Christie")
        self.assertEqual(clean_author(""), "")
        self.assertNotIn(" ,", clean_author("by A Christie , B Person (Illustrator)"))

    def test_matches_show_only_the_primary_author(self):
        self.assertEqual(primary_author(
            "by Agatha Christie, Hugh Fraser (Narrator)  Format: Audio Cassette"),
            "Agatha Christie")

    def test_price(self):
        self.assertEqual(price_value("£3.99"), 3.99)
        self.assertEqual(price_value("£1,234.50"), 1234.5)
        self.assertEqual(price_value("£11"), 11.0)
        self.assertIsNone(price_value(None))
        self.assertIsNone(price_value("free"))

    def test_dates_lose_their_timezone_for_excel(self):
        from christie_hunter.excel import as_date
        d = as_date("2026-11-05T09:00:00+00:00")
        self.assertIsInstance(d, datetime)
        self.assertIsNone(d.tzinfo)
        self.assertIsNone(as_date("nonsense"))


class TestRouting(unittest.TestCase):
    def test_each_record_lands_on_exactly_one_sheet(self):
        matches, review, unreadable, gone = route(RECORDS)
        self.assertEqual([a for a, _ in matches], ["B000000002", "B000000001"])
        self.assertEqual([a for a, _ in review], ["B000000003", "B000000005"])
        self.assertEqual([a for a, _ in unreadable], ["B000000004"])
        self.assertEqual(sorted(a for a, _ in gone), ["B000000006", "B000000007"])

    def test_matches_are_sorted_by_publisher_then_title(self):
        matches, _, _, _ = route(RECORDS)
        self.assertEqual([r.get("publisher") for _, r in matches],
                         ["Audio Partners", "Digital Ink"])

    def test_an_excluded_record_that_was_never_a_match_is_not_reported(self):
        _, _, _, gone = route(RECORDS)
        self.assertNotIn("B000000008", [a for a, _ in gone])


class TestWorkbook(unittest.TestCase):
    def setUp(self):
        self.wb, self.counts = build_workbook(RECORDS, generated=datetime(2026, 11, 5, 12))
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "wb.xlsx"
        self.wb.save(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sheets_and_counts(self):
        self.assertEqual(self.wb.sheetnames,
                         ["Summary", "Matches", "Needs review", "Could not read",
                          "Gone or changed", "_state"])
        self.assertEqual(self.counts, {"records": 8, "match": 2, "review": 2,
                                       "unreadable": 1, "gone": 2})

    def test_amazon_links_are_live_hyperlinks(self):
        ws = self.wb["Matches"]
        cells = [ws.cell(row=r, column=6) for r in range(2, 4)]
        for cell in cells:
            self.assertEqual(cell.value, "Open on Amazon")
            self.assertTrue(cell.hyperlink.target.startswith("https://www.amazon.co.uk/dp/"))

    def test_no_screenshots_or_local_paths_are_in_the_workbook(self):
        # evidence stays in output/evidence for the technical user
        strings = [str(c.value) for row in self.wb["Matches"].iter_rows()
                   for c in row if c.value is not None]
        joined = " ".join(strings)
        self.assertNotIn("evidence", joined)
        self.assertNotIn(".png", joined)
        self.assertNotIn("output/", joined)

    def test_header_is_frozen_and_filterable(self):
        ws = self.wb["Matches"]
        self.assertEqual(ws.freeze_panes, "B2")
        self.assertTrue(ws.auto_filter.ref.startswith("A1:"))

    def test_empty_publisher_is_shown_as_a_dash_not_a_blank(self):
        ws = self.wb["Needs review"]
        row = [r for r in ws.iter_rows(min_row=2, values_only=True)
               if r[0] == "A.B.C. Murders"][0]
        self.assertEqual(row[2], "—")

    def test_the_review_sheet_explains_why_in_short_form(self):
        ws = self.wb["Needs review"]
        whys = {r[5] for r in ws.iter_rows(min_row=2, values_only=True)}
        self.assertEqual(whys, {"Author credit unclear", "No publisher shown"})
        # and the long form appears once, on the summary
        summary = " ".join(str(c.value) for row in self.wb["Summary"].iter_rows()
                           for c in row if c.value)
        self.assertIn("Publisher name is ambiguous", summary)

    def test_reviewer_notes_column_is_last_and_empty(self):
        ws = self.wb["Matches"]
        self.assertEqual(ws.cell(row=1, column=ws.max_column).value, "Reviewer notes")
        self.assertTrue(all(ws.cell(row=r, column=ws.max_column).value is None
                            for r in range(2, ws.max_row + 1)))


class FakeStore:
    def __init__(self, records):
        self.records = records


class TestLivingDocument(unittest.TestCase):
    """Notes written by the recipient must survive a re-export."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = {"output_dir": self.tmp.name}
        self.path = Path(self.tmp.name) / "wb.xlsx"

    def tearDown(self):
        self.tmp.cleanup()

    def annotate(self, asin_note):
        wb = load_workbook(self.path)
        ws = wb["Matches"]
        col = ws.max_column
        for r in range(2, ws.max_row + 1):
            asin = ws.cell(row=r, column=col - 2).value
            if asin in asin_note:
                ws.cell(row=r, column=col, value=asin_note[asin])
        wb.save(self.path)

    def test_notes_survive_a_reexport(self):
        export(self.cfg, self.path, store=FakeStore(RECORDS))
        self.annotate({"B000000001": "emailed publisher 05/11"})
        export(self.cfg, self.path, store=FakeStore(RECORDS))
        wb = load_workbook(self.path)
        ws = wb["Matches"]
        got = {ws.cell(row=r, column=ws.max_column - 2).value:
               ws.cell(row=r, column=ws.max_column).value
               for r in range(2, ws.max_row + 1)}
        self.assertEqual(got["B000000001"], "emailed publisher 05/11")
        self.assertIsNone(got["B000000002"])

    def test_a_note_is_not_lost_when_its_listing_disappears(self):
        export(self.cfg, self.path, store=FakeStore(RECORDS))
        self.annotate({"B000000001": "keep an eye on this"})
        # the listing goes away before the next export
        shrunk = {a: r for a, r in RECORDS.items() if a != "B000000001"}
        export(self.cfg, self.path, store=FakeStore(shrunk))
        notes = read_notes(self.path)
        self.assertEqual(notes["B000000001"]["notes"], "keep an eye on this")

    def test_notes_are_keyed_by_asin_not_by_row(self):
        export(self.cfg, self.path, store=FakeStore(RECORDS))
        self.annotate({"B000000002": "checked"})
        # a new match is added, which changes every row position
        grown = dict(RECORDS)
        grown["B000000009"] = {**RECORDS["B000000001"], "title": "A New One",
                               "publisher": "Aardvark"}
        export(self.cfg, self.path, store=FakeStore(grown))
        wb = load_workbook(self.path)
        ws = wb["Matches"]
        got = {ws.cell(row=r, column=ws.max_column - 2).value:
               ws.cell(row=r, column=ws.max_column).value
               for r in range(2, ws.max_row + 1)}
        self.assertEqual(got["B000000002"], "checked")

    def test_the_first_export_is_stamped(self):
        export(self.cfg, self.path, store=FakeStore(RECORDS))
        notes = read_notes(self.path)
        self.assertTrue(all(e["first_exported"] for e in notes.values()))


if __name__ == "__main__":
    unittest.main()
