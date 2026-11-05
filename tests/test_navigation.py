"""Navigation failure handling: no single page may end a run.

These tests drive `search.py` with a fake `goto` — no browser, no network.
"""

import tempfile
import unittest
from pathlib import Path

from christie_hunter.crawler import Crawler
from christie_hunter.errors import (Blocked, DeadPage, StopRun, Transient,
                                    UnexpectedPage)
from christie_hunter.search import Task, classify_pending, run_task
from christie_hunter.store import Store

PRODUCT_HTML = '<span id="productTitle">x</span>'


class FakeCrawler(Crawler):
    """A Crawler whose navigation is a dict lookup instead of a browser."""

    def __init__(self, cfg, store, responses):
        super().__init__(cfg, store)
        self.responses = responses      # url -> html, or an exception to raise
        self.fetched = []

    def _check_stop(self):
        pass

    def _budget_left(self):
        return True

    def goto(self, url, expect=None, referer=None):
        self.navs += 1
        self.fetched.append({"url": url, "expect": expect, "referer": referer})
        r = self.responses.get(url)
        if isinstance(r, Exception):
            raise r
        return r if r is not None else PRODUCT_HTML

    def parse_product(self, asin, content):
        return "match", {"title": f"T{asin}", "author_line": "by Agatha Christie",
                         "publisher": "P", "url": f"https://x/dp/{asin}",
                         "source": self.store.data["sources"].get(asin, "")}

    def save_evidence(self, asin, rec):
        rec["evidence"] = ["shot.png"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = {"output_dir": self.tmp.name, "politeness": {},
                    "licensed_publishers": [], "prefilter_cards": True,
                    "max_asin_attempts": 3}
        self.store = Store(Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def crawler(self, responses):
        return FakeCrawler(self.cfg, self.store, responses)


class TestClassify(Base):
    def test_dead_asin_is_recorded_not_retried_and_not_a_block(self):
        self.store.add_pending(["A"], "src")
        c = self.crawler({"https://www.amazon.co.uk/dp/A": DeadPage("status 404")})
        classified = classify_pending(c, self.store)
        self.assertEqual(classified, 0)
        rec = self.store.records["A"]
        self.assertEqual(rec["classification"], "excluded_dead_asin")
        self.assertEqual(self.store.pending, [])
        self.assertEqual(len(c.fetched), 1)          # one fetch, no cooldown loop

    def test_one_bad_page_does_not_stop_the_queue(self):
        self.store.add_pending(["A", "B", "C"], "src")
        c = self.crawler({"https://www.amazon.co.uk/dp/A":
                          UnexpectedPage("not a product page")})
        classify_pending(c, self.store)
        self.assertEqual(self.store.records["B"]["classification"], "match")
        self.assertEqual(self.store.records["C"]["classification"], "match")
        self.assertNotIn("A", self.store.records)
        self.assertIn("A", self.store.pending)       # requeued for a later pass

    def test_repeat_offender_is_flagged_for_review_after_three_tries(self):
        self.store.add_pending(["A"], "src")
        c = self.crawler({"https://www.amazon.co.uk/dp/A": Transient("timeout")})
        for _ in range(3):
            classify_pending(c, self.store)
        self.assertEqual(self.store.records["A"]["classification"],
                         "unclear_fetch_failed")
        self.assertEqual(self.store.pending, [])
        self.assertEqual(len(c.fetched), 3)

    def test_a_block_requeues_and_the_run_continues(self):
        self.store.add_pending(["A", "B"], "src")
        c = self.crawler({"https://www.amazon.co.uk/dp/A": Blocked("blocked")})
        classify_pending(c, self.store)
        self.assertEqual(self.store.records["B"]["classification"], "match")
        self.assertIn("A", self.store.pending)
        self.assertEqual(self.store.data["attempts"]["A"]["count"], 1)

    def test_repeated_blocks_stop_the_run_cleanly(self):
        # StopRun is the only thing that ends a phase; it must still be raised
        # when the block is real (goto raises it once cool-downs are spent)
        self.store.add_pending(["A", "B"], "src")
        c = self.crawler({"https://www.amazon.co.uk/dp/A":
                          StopRun("blocked 4 times in a row")})
        with self.assertRaises(StopRun):
            classify_pending(c, self.store)

    def test_three_failures_in_a_row_stop_the_pass_instead_of_flagging_the_queue(self):
        # a site-wide soft block must not turn 600 pending ASINs into 600
        # "unclear_fetch_failed" records
        self.store.add_pending(["A", "B", "C", "D", "E"], "src")
        c = self.crawler({f"https://www.amazon.co.uk/dp/{a}": UnexpectedPage("small page")
                          for a in "ABCDE"})
        with self.assertRaises(StopRun):
            classify_pending(c, self.store)
        self.assertEqual(self.store.records, {})        # nothing mislabelled
        # the item being fetched when we stopped stays in in_flight and is
        # requeued when state.json is next loaded (same as after a kill -9)
        still_queued = set(self.store.pending) | set(self.store.data["in_flight"])
        self.assertEqual(still_queued, set("ABCDE"))
        self.assertEqual(len(self.store.data["in_flight"]), 1)

    def test_product_pages_are_fetched_with_a_referer(self):
        self.store.add_pending(["A"], "src", referer="https://www.amazon.co.uk/s?k=x")
        c = self.crawler({})
        classify_pending(c, self.store)
        self.assertEqual(c.fetched[0]["expect"], "product")
        self.assertEqual(c.fetched[0]["referer"], "https://www.amazon.co.uk/s?k=x")


class TestEnumerate(Base):
    def _task(self, max_pages=5):
        return Task("t", lambda p: f"https://www.amazon.co.uk/s?k=x&page={p}",
                    max_pages, "title|stripbooks")

    def page(self, *asins):
        return "".join(f'<div data-asin="{a}"></div>' for a in asins)

    def test_an_unreadable_page_does_not_retire_the_search(self):
        c = self.crawler({"https://www.amazon.co.uk/s?k=x&page=1":
                          UnexpectedPage("not a search page")})
        run_task(c, self.store, self._task(), rescan=False, fresh_days=7)
        prog = self.store.progress("t")
        self.assertFalse(prog["done"])          # still stale: it will be retried
        self.assertEqual(prog["next_page"], 1)

    def test_a_genuine_end_of_results_does_retire_the_search(self):
        c = self.crawler({"https://www.amazon.co.uk/s?k=x&page=1": ""})
        run_task(c, self.store, self._task(), rescan=False, fresh_days=7)
        prog = self.store.progress("t")
        self.assertTrue(prog["done"])

    def test_pages_are_queued_with_the_page_url_as_referer(self):
        c = self.crawler({"https://www.amazon.co.uk/s?k=x&page=1":
                          self.page("B000000001", "B000000002")})
        run_task(c, self.store, self._task(max_pages=1), rescan=False,
                 fresh_days=7)
        self.assertEqual(sorted(self.store.pending), ["B000000001", "B000000002"])
        self.assertEqual(self.store.referer("B000000001"),
                         "https://www.amazon.co.uk/s?k=x&page=1")
        self.assertEqual(c.fetched[0]["expect"], "search")
        self.assertEqual(c.fetched[0]["referer"], "https://www.amazon.co.uk/")

    def test_paging_arrives_from_the_previous_page(self):
        c = self.crawler({"https://www.amazon.co.uk/s?k=x&page=1": self.page("B000000001"),
                          "https://www.amazon.co.uk/s?k=x&page=2": self.page("B000000002")})
        run_task(c, self.store, self._task(), rescan=False, fresh_days=7)
        self.assertEqual([f["referer"] for f in c.fetched[:2]],
                         ["https://www.amazon.co.uk/",
                          "https://www.amazon.co.uk/s?k=x&page=1"])

    def test_search_budget_exhaustion_leaves_the_task_resumable(self):
        self.store.progress("t")["next_page"] = 3
        c = self.crawler({})
        c.stop_requested = False
        run_task(c, self.store, self._task(), rescan=False, fresh_days=7)
        # 5 pages max, started at page 3, all empty -> finished, done, page 6
        self.assertEqual(self.store.progress("t")["next_page"], 6)


class TestPageSignals(unittest.TestCase):
    """The string checks that decide 'blocked' vs 'fine' vs 'wrong page'."""

    def setUp(self):
        self.cfg = {"output_dir": tempfile.gettempdir(), "politeness": {},
                    "licensed_publishers": []}
        self.crawler = Crawler(self.cfg, Store(Path(tempfile.gettempdir()) / "no.json"))

    def test_amazons_404_page_is_not_a_block(self):
        # the exact boilerplate that made a dead ASIN look like a ban
        dead = ("<html><body><h4>Looking for something?</h4>"
                "We're sorry. The Web address you entered is not a functioning "
                "page on our site.<p>To discuss automated access to Amazon data "
                "please contact api-services-support@amazon.com.</p></body></html>")
        self.assertFalse(self.crawler._is_captcha(dead))

    def test_captcha_interstitial_is_a_block(self):
        captcha = ("<html><body>Enter the characters you see below"
                   "<img src='/captcha/x.jpg'>"
                   "To discuss automated access to Amazon data please contact "
                   "api-services-support@amazon.com</body></html>")
        self.assertTrue(self.crawler._is_captcha(captcha))

    def test_a_large_page_is_never_treated_as_an_interstitial(self):
        big = ("<html><body>" + "x" * 200_000 +
               "enter the characters you see below</body></html>")
        self.assertFalse(self.crawler._is_captcha(big))

    def test_expectation_check(self):
        self.assertTrue(self.crawler._looks_like(PRODUCT_HTML, "product"))
        self.assertFalse(self.crawler._looks_like(PRODUCT_HTML, "search"))
        self.assertFalse(self.crawler._looks_like("<html>nope</html>", "product"))
        self.assertTrue(self.crawler._looks_like(
            '<div data-component-type="s-search-result">', "search"))
        self.assertTrue(self.crawler._looks_like("No results for B00X", "search"))

    def test_prime_video_pages_are_recognised_as_non_books(self):
        video = ('<html><body><div id="nav-belt"></div>'
                 '<h1>Witness For The Prosecution</h1>'
                 '<a href="/gp/video/detail/B01AG191RA">Watch</a></body></html>')
        self.assertTrue(self.crawler._is_non_book_page(video))
        self.assertFalse(self.crawler._is_non_book_page(PRODUCT_HTML))

    def test_a_book_page_is_never_treated_as_a_non_book_page(self):
        # a real book page with a Prime Video carousel must stay a book page:
        # the exemption is only consulted when #productTitle is absent
        book = PRODUCT_HTML + '<a href="/gp/video/detail/B01AG191RA">Watch</a>'
        self.assertTrue(self.crawler._looks_like(book, "product"))

    def test_card_prefilter_keeps_anything_it_is_unsure_about(self):
        want = self.crawler._card_wanted
        self.assertTrue(want("Murder on the Orient Express by Agatha Christie"))
        self.assertFalse(want("Fotherington-Tomas and Blue Murder on the Orient"))
        self.assertFalse(want("Sponsored Prime Video - watch now"))
        self.assertTrue(want(""))               # unrendered card: keep
        self.assertTrue(want("short"))          # too little text to judge: keep


if __name__ == "__main__":
    unittest.main()
