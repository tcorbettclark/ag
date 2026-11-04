#!/usr/bin/env python3
"""
christie_hunter — find English-language Agatha Christie books/ebooks on
amazon.co.uk that are NOT published by HarperCollins or any of its imprints.

Resumable: all progress lives in state.json (ASIN-keyed). Interrupt anytime
with Ctrl-C; the next run picks up exactly where this one left off.
Completed searches are re-enumerated only after `search_rescan_days`.

Usage:
    uv run python christie_hunter.py                  # normal run
    uv run python christie_hunter.py --limit 50        # cap navigations this run
    uv run python christie_hunter.py --rescan          # force full search re-enumeration
    uv run python christie_hunter.py --classify-only   # skip search phase
    uv run python christie_hunter.py --search-only     # skip classification phase
"""

import argparse
import csv
import json
import logging
import random
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, quote

import christie_works
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = "https://www.amazon.co.uk"
AUTHOR_PAGE = "https://www.amazon.co.uk/Agatha-Christie/e/B000APENBC"
STATE_FILE = Path("state.json")
CONFIG_FILE = Path("config.json")

# --- HarperCollins imprint matching -----------------------------------------

# Definite HarperCollins imprints -> excluded_hc
HC_DEFINITE = [
    r"harper\s*-?\s*collins",                                  # HarperCollins, Harper Collins
    r"william\s+collins",
    r"collins\s+crime\s+club",
    r"collins\s+gems?\b",
    r"\bcollins\b",                                            # bare Collins = historic HC imprint
    r"\bfontana\b",
    # NB: no `fiction` here — "HarperFiction" is a licensed audio publisher (config.json)
    r"harper\s+(?:press|voyager|element|audio|design|luxe|one|true\s*crime|children)",
    r"harpercollins",                                          # no-space variants
    r"william\s+morrow",
    r"4\s*-?\s*th\s+estate|fourth\s+estate",
    r"borough\s+press",
]
# Ambiguous tokens that historically also exist outside HC -> unclear (human review)
HC_AMBIGUOUS = [
    r"\bharper\b",
    r"\bavon\b",
]

# Author-line contamination: Christie credited, but not as the author
AUTHOR_CONTAMINATION = [
    "foreword", "afterword", "introduction", "preface", "inspired", "reimagin",
    "featuring", "based on", "creator of", "creator", "edited by", "curated by",
]

BLOCK_MARKERS = [
    "robot check", "enter the characters you see below",
    "api-services-support@amazon.com", "to discuss automated access",
    "sorry, something went wrong", "access denied", "captcha",
    "to continue, please type the characters",
]

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]
VIEWPORTS = [(1920, 1080), (1440, 900), (1536, 864), (1366, 768), (1280, 800)]

ASIN_RE = re.compile(r'(?:data-asin="|/(?:dp|gp/product)/)([A-Z0-9]{10})')


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- state --------------------------------------------------------------------

class Store:
    """Persistent, ASIN-keyed record of everything we've seen."""

    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            with open(path) as f:
                self.data = json.load(f)
        else:
            self.data = {"records": {}, "pending": [], "in_flight": [],
                         "search_progress": {}, "runs": []}
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

    def known(self, asin):
        return asin in self.data["records"] or asin in self.data["pending"]

    def add_pending(self, asins, source):
        added = 0
        for a in asins:
            if not self.known(a):
                self.data["pending"].append(a)
                # remember where it came from (used when classified)
                self.data.setdefault("sources", {})[a] = source
                added += 1
        return added

    def take_pending(self):
        if not self.data["pending"]:
            return None
        asin = self.data["pending"].pop(0)
        self.data["in_flight"].append(asin)
        self.save()
        return asin

    def record(self, asin, **fields):
        rec = self.data["records"].setdefault(asin, {"first_seen": now_iso()})
        rec.update(fields)
        rec["last_seen"] = now_iso()
        if asin in self.data["in_flight"]:
            self.data["in_flight"].remove(asin)
        self.save()

    def progress(self, key):
        return self.data["search_progress"].setdefault(
            key, {"next_page": 1, "done": False, "last_enumerated": None})


class StopRun(Exception):
    """Raised when the run must end (budget, blocks, or Ctrl-C)."""


# --- crawler ------------------------------------------------------------------

class Crawler:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.navs = 0
        self.until_refresh = self._refresh_in()
        self.stop_requested = False
        # authorised licensees (BBC audio, Laurence King puzzle books, ...) —
        # never rights violations, exclude outright like HC
        self.licensed = [re.compile(p, re.I)
                         for p in cfg.get("licensed_publishers", [])]
        # soft cap: fraction of the nav budget reserved for classification
        self.search_budget = None

    def _refresh_in(self):
        p = self.cfg["politeness"]
        return random.randint(p["session_refresh_min"], p["session_refresh_max"])

    # -- sessions --
    def start(self, pw):
        self.pw = pw
        self.browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"])
        self.new_session()

    def new_session(self, warmup=True):
        if self.context:
            try:
                self.context.close()
            except Exception:
                pass
        ua = random.choice(USER_AGENTS)
        vw = random.choice(VIEWPORTS)
        self.context = self.browser.new_context(
            user_agent=ua, viewport={"width": vw[0], "height": vw[1]},
            locale="en-GB", timezone_id="Europe/London")
        self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "window.chrome = window.chrome || {runtime: {}};")
        self.page = self.context.new_page()
        # cap Playwright's hidden 30s element auto-waits: missing selectors on a
        # page should fail fast (8s), not silently stall classification
        self.page.set_default_timeout(8000)
        if warmup:
            # look human: start at the homepage, scroll a bit
            try:
                self.page.goto(BASE, timeout=45000, wait_until="domcontentloaded")
                self._human_scroll()
                self._pause()
            except Exception:
                pass

    def close(self):
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
        except Exception:
            pass

    # -- human behaviour --
    def _pause(self):
        p = self.cfg["politeness"]
        if random.random() < p["long_pause_chance"]:
            d = random.uniform(p["long_pause_min_s"], p["long_pause_max_s"])
            logging.info("long human-like pause: %.0fs", d)
            time.sleep(d)
        else:
            time.sleep(random.uniform(p["min_delay_s"], p["max_delay_s"]))

    def _human_scroll(self):
        for _ in range(random.randint(1, 3)):
            self.page.mouse.wheel(0, random.randint(200, 900))
            time.sleep(random.uniform(0.4, 1.6))

    def _budget_left(self):
        return self.navs < self.cfg["politeness"]["max_navs_per_run"]

    def _check_stop(self):
        if self.stop_requested:
            raise StopRun("interrupted (Ctrl-C)")
        if not self._budget_left():
            raise StopRun("navigation budget for this run exhausted")

    # -- navigation with block detection --
    def goto(self, url):
        backoffs = [300, 900, 2700]  # 5 min, 15 min, 45 min
        for attempt in range(4):
            self._check_stop()
            self.navs += 1
            self.until_refresh -= 1
            if self.until_refresh <= 0:
                logging.info("starting a fresh browser session")
                self.new_session()
                self.until_refresh = self._refresh_in()
            try:
                resp = self.page.goto(url, timeout=45000,
                                      wait_until="domcontentloaded")
                self._dismiss_cookie_banner()
                self._human_scroll()
                content = self.page.content()
                status = resp.status if resp else 200
                if status in (429, 503) or self._is_blocked(content):
                    wait = backoffs[attempt] if attempt < 3 else 0
                    logging.warning("block detected (status %s) on %s", status, url)
                    if wait == 0:
                        raise StopRun("blocked repeatedly; resumable, run again later")
                    logging.warning("cooling down %.0f min then new session",
                                    wait / 60)
                    time.sleep(wait)
                    self.new_session()
                    continue
                self._pause()
                return content
            except PWTimeout:
                logging.warning("timeout on %s", url)
                self.new_session()
                continue
        raise StopRun("repeated failures on " + url)

    def _is_blocked(self, content):
        low = content[:6000].lower()
        return any(m in low for m in BLOCK_MARKERS)

    def _dismiss_cookie_banner(self):
        try:
            self.page.locator("#sp-cc-accept").click(timeout=2500)
        except Exception:
            pass

    # -- page parsing --
    @staticmethod
    def extract_asins(content):
        asins = []
        for m in ASIN_RE.finditer(content):
            a = m.group(1)
            if a not in asins:
                asins.append(a)
        return asins

    def parse_product(self, asin, content):
        """Extract and classify one product page. Returns (classification, fields)."""
        def txt(sel):
            try:
                return self.page.locator(sel).first.inner_text().strip()
            except Exception:
                return ""

        title = txt("#productTitle")
        # normalize the odd bidi chars Amazon puts in detail bullets
        body = re.sub(r"[\u200e\u200f\u00a0]", " ",
                      self.page.locator("body").inner_text())

        def detail(key):
            m = re.search(rf"{key}\s*:\s*([^\n]+)", body)
            if not m:
                return None
            v = m.group(1).strip()
            v = re.sub(r"\s*\([^)]*\)\s*$", "", v)  # trailing "(3 Dec. 2015)"
            return v

        byline = txt("#bylineInfo")
        publisher = detail("Publisher")
        language = detail("Language")
        fmt = detail("Format")

        fields = {
            "title": title, "author_line": byline, "publisher": publisher,
            "language": language, "format": fmt,
            "url": f"{BASE}/dp/{asin}",
            "source": self.store.data.get("sources", {}).get(asin, ""),
        }

        # --- classification pipeline (order matters; first exclusion wins) ---

        def hc_class(pub_value):
            """'excluded_harpercollins' | 'unclear_publisher' | None (not HC-ish)."""
            pub = (pub_value or "").lower()
            if not pub:
                return None
            if any(re.search(p, pub) for p in HC_DEFINITE):
                return "excluded_harpercollins"
            if any(re.search(p, pub) for p in HC_AMBIGUOUS):
                return "unclear_publisher"
            return None

        # 1. authorship: Christie must be credited somewhere on the page
        low = byline.lower()
        if "agatha christie" not in low:
            if not title:
                return "excluded_not_christie", fields  # not a product page (Prime Video etc.)
            if not byline:
                # a book page whose author line we failed to parse -> human review
                return "unclear_parse_fail", fields
            return "excluded_not_christie", fields

        # 2. publisher: a definite HC imprint excludes outright, whoever the author
        #    is and whatever the language/availability (e.g. the Lucy Foley
        #    reimagining is by Foley, but HarperCollins settles it on its own)
        if hc_class(publisher) == "excluded_harpercollins":
            return "excluded_harpercollins", fields

        # 3. licensed publishers (BBC, Audible, Orion, ... — see config.json) are
        #    authorised editions, so they are excluded exactly like HC
        if publisher and any(p.search(publisher) for p in self.licensed):
            return "excluded_licensed", fields

        # 4. authorship quality: Christie must be the primary, uncontaminated author
        if any(c in low for c in AUTHOR_CONTAMINATION):
            return "unclear_author", fields
        names = re.split(r"\bby\b", byline, maxsplit=1)[-1]
        first = re.split(r"\band\b|&|,", names)[0]
        first = re.sub(r"\(.*?\)", "", first).strip().lower()
        if not first.startswith("agatha christie"):
            return "unclear_author", fields  # e.g. "by Lucy Foley and Agatha Christie"

        # 4. language
        if language and language.strip().lower() != "english":
            return "excluded_not_english", fields

        # 5. buyable new, right now
        buyable, price = self._buyability(content, body)
        fields["price"] = price
        if not buyable:
            return "excluded_unavailable", fields

        # 6. publisher final gate: ambiguous -> review, missing -> review, else match
        if publisher:
            if hc_class(publisher) == "unclear_publisher":
                return "unclear_publisher", fields
            return "match", fields
        return "unclear_no_publisher", fields

    def _first_price(self):
        """First .a-offscreen element that looks like a GBP price (layout-agnostic)."""
        try:
            els = self.page.locator(".a-offscreen")
            for i in range(min(25, els.count())):
                t = els.nth(i).inner_text().strip()
                if re.match(r"^\u00a3[\d,]+(\.\d{2})?$", t):
                    return t
        except Exception:
            pass
        return None

    def _buyability(self, content, body):
        """Return (buyable_now, price). Buy signals win over unavailability text."""
        price = self._first_price()
        # classic layouts: dedicated buy buttons
        for sel in ["#add-to-cart-button", "#buy-now-button", "#oneClickBuyButton"]:
            try:
                if self.page.locator(sel).first.is_visible():
                    return True, price
            except Exception:
                continue
        # new Kindle layout: generic submit input labelled "Buy now"
        if 'value="Buy now' in content:
            return True, price
        # price shown in the buy area at all (e.g. "orderable" without JS buttons)
        if price:
            return True, price
        # explicit unavailability (and nothing buyable found above)
        if "currently unavailable" in body.lower():
            return False, None
        return False, None

    def save_evidence(self, asin, rec):
        out = Path(self.cfg["output_dir"]) / "evidence"
        out.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shot = out / f"{asin}_{ts}.png"
        html = out / f"{asin}_{ts}.html"
        try:
            self.page.screenshot(path=str(shot), full_page=True)
            html.write_text(self.page.content(), encoding="utf-8")
            rec["evidence"] = [str(shot), str(html)]
            rec["evidence_timestamp"] = ts
        except Exception as e:
            logging.warning("evidence capture failed for %s: %s", asin, e)


# --- search phase ---------------------------------------------------------------

def search_url(index, query, page_no):
    return f"{BASE}/s?{urlencode({'k': query, 'i': index, 'page': page_no})}"


def author_search_url(index, page_no):
    rh = quote("p_27:Agatha Christie")
    return f"{BASE}/s?i={index}&rh={rh}&page={page_no}"


class Task:
    def __init__(self, key, url_fn, max_pages, source):
        self.key, self.url_fn, self.max_pages, self.source = key, url_fn, max_pages, source


def build_tasks(cfg, limit_indices=None):
    s = cfg["search"]
    tasks = []
    for idx in s["indices"]:
        if limit_indices and idx not in limit_indices:
            continue
        for q in s["generic_queries"]:
            tasks.append(Task(f"generic|{idx}|{q}",
                              lambda p, q=q, i=idx: search_url(i, q, p),
                              s["generic_max_pages"], f"generic|{idx}"))
        # author-strict search (every book Amazon attributes to her)
        tasks.append(Task(f"author|{idx}",
                          lambda p, i=idx: author_search_url(i, p),
                          s["author_max_pages"], f"author|{idx}"))
        # per-title searches against the canonical works list
        for work, query in christie_works.all_search_queries():
            tasks.append(Task(f"title|{idx}|{query}",
                              lambda p, q=query, i=idx: search_url(i, q, p),
                              s["title_max_pages"], f"title|{idx}|{work}"))
    return tasks


def run_task(crawler, store, task, rescan):
    prog = store.progress(task.key)
    fresh_days = cfg["search_rescan_days"]
    if prog["done"] and not rescan and prog["last_enumerated"]:
        last = datetime.fromisoformat(prog["last_enumerated"])
        if (datetime.now(timezone.utc) - last).days < fresh_days:
            logging.debug("skipping fresh-enough search %s", task.key)
            return
    page_no = 1 if rescan else prog["next_page"]
    logging.info("enumerating %s from page %d", task.key, page_no)
    completed = False
    try:
        while page_no <= task.max_pages:
            if crawler.stop_requested:
                return  # do NOT mark done; resume next run
            if crawler.search_budget is not None and crawler.navs >= crawler.search_budget:
                logging.info("search budget reached mid-task at %s page %d",
                             task.key, page_no)
                return  # resume this task next run
            content = crawler.goto(task.url_fn(page_no))
            asins = crawler.extract_asins(content)
            if not asins:
                break  # exhausted
            new = store.add_pending([a for a in asins if not store.known(a)],
                                    task.source)
            if new:
                logging.info("  %s: %d new ASINs queued (total pending %d)",
                             task.key, new, len(store.data["pending"]))
            prog["next_page"] = page_no + 1
            prog["last_enumerated"] = now_iso()
            store.save()
            page_no += 1
        completed = True
    finally:
        if completed:
            prog["done"] = True
            prog["last_enumerated"] = now_iso()
            store.save()


def author_page_task(crawler, store, rescan):
    prog = store.progress("authorpage")
    if prog["done"] and not rescan:
        return
    logging.info("enumerating author page")
    try:
        content = crawler.goto(AUTHOR_PAGE)
        asins = crawler.extract_asins(content)
        new = store.add_pending(asins, "authorpage")
        logging.info("  author page: %d new ASINs queued", new)
    finally:
        prog["done"] = True
        prog["last_enumerated"] = now_iso()
        store.save()


# --- classification phase ---------------------------------------------------------

def classify_pending(crawler, store):
    n = 0
    while True:
        crawler._check_stop()
        asin = store.take_pending()
        if not asin:
            break
        if asin in store.records:
            continue
        content = crawler.goto(f"{BASE}/dp/{asin}")
        classification, fields = crawler.parse_product(asin, content)
        rec = dict(fields)
        rec["classification"] = classification
        if classification == "match" or classification.startswith("unclear"):
            crawler.save_evidence(asin, rec)
            logging.info("  %s -> %s: %s (%s)",
                         asin, classification.upper(),
                         fields["title"][:60], fields.get("publisher"))
        store.record(asin, **rec)
        n += 1
    return n


# --- reports -----------------------------------------------------------------------

def write_reports(store, cfg):
    out = Path(cfg["output_dir"])
    out.mkdir(exist_ok=True)

    def rows(cls_pred):
        for asin, rec in store.records.items():
            if cls_pred(rec.get("classification")):
                yield {**rec, "asin": asin}

    cols = ["asin", "classification", "title", "author_line", "format",
            "publisher", "language", "price", "url", "source",
            "first_seen", "last_seen", "evidence"]

    def write_csv(path, pred):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows(pred):
                ev = r.get("evidence")
                r = dict(r)
                r["evidence"] = ";".join(ev) if isinstance(ev, list) else (ev or "")
                w.writerow(r)

    write_csv(out / "matches.csv", lambda c: c == "match")
    write_csv(out / "unclear.csv", lambda c: bool(c) and c.startswith("unclear"))
    write_csv(out / "all_records.csv", lambda c: True)
    logging.info("reports written to %s", out)


# --- main ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int,
                    help="max navigations this run (overrides config)")
    ap.add_argument("--rescan", action="store_true",
                    help="force re-enumeration of completed searches")
    ap.add_argument("--classify-only", action="store_true")
    ap.add_argument("--search-only", action="store_true")
    args = ap.parse_args()

    global cfg
    cfg = json.loads(CONFIG_FILE.read_text())
    if args.limit:
        cfg["politeness"]["max_navs_per_run"] = args.limit

    Path(cfg["output_dir"]).mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(Path(cfg["output_dir"]) / "run.log"),
                  logging.StreamHandler(sys.stdout)])

    store = Store(STATE_FILE)
    logging.info("state: %d classified, %d pending classification",
                 len(store.records), len(store.data["pending"]))

    crawler = Crawler(cfg, store)
    # reserve ~60% of the nav budget for search; the rest classifies this run
    crawler.search_budget = max(1, int(cfg["politeness"]["max_navs_per_run"] * 0.6))

    def on_sig(signum, frame):
        logging.info("signal received — stopping after the current page "
                    "(progress is saved; re-run to resume)")
        crawler.stop_requested = True
    signal.signal(signal.SIGINT, on_sig)
    signal.signal(signal.SIGTERM, on_sig)

    run_start = now_iso()
    reason = "completed"
    try:
        time.sleep(random.uniform(1, 10))  # don't start like a machine
        with sync_playwright() as pw:
            crawler.start(pw)
            if not args.classify_only:
                author_page_task(crawler, store, args.rescan)
                for task in build_tasks(cfg):
                    run_task(crawler, store, task, args.rescan)
                    if crawler.stop_requested or not crawler._budget_left():
                        break
            if not args.search_only:
                if crawler.stop_requested or not crawler._budget_left():
                    raise StopRun("budget/interrupt before classification")
                classify_pending(crawler, store)
    except StopRun as e:
        reason = str(e)
        logging.warning("run stopped: %s", reason)
    finally:
        crawler.close()
        store.data["runs"].append({
            "started": run_start, "ended": now_iso(),
            "navs": crawler.navs, "end_reason": reason})
        store.save()
        write_reports(store, cfg)
        n_match = sum(1 for r in store.records.values()
                      if r.get("classification") == "match")
        n_unclear = sum(1 for r in store.records.values()
                        if (r.get("classification") or "").startswith("unclear"))
        logging.info(
            "run done (%s): %d navs | total: %d records, %d matches, "
            "%d unclear, %d still pending. Resume by re-running.",
            reason, crawler.navs, len(store.records), n_match, n_unclear,
            len(store.data["pending"]))


if __name__ == "__main__":
    main()