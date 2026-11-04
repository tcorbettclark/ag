"""The Playwright crawler: sessions, polite navigation, and page classification."""

import logging
import random
import re
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout

from .constants import (ASIN_RE, AUTHOR_CONTAMINATION, BASE, BLOCK_MARKERS,
                        HC_AMBIGUOUS, HC_DEFINITE, USER_AGENTS, VIEWPORTS)
from .errors import StopRun


class Crawler:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.navs = 0
        self.phase = "idle"          # search | classify — only used in debug logs
        self.budget = None           # set by the phase that is running
        self.until_refresh = self._refresh_in()
        self.stop_requested = False
        # authorised licensees (BBC audio, Laurence King puzzle books, ...) —
        # never rights violations, exclude outright like HC
        self.licensed = [re.compile(p, re.I)
                         for p in cfg.get("licensed_publishers", [])]

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
            # look human: start at the homepage, scroll a bit. Deliberately not
            # counted as a navigation: it is outside the budget either way, and
            # with a wall-clock budget it is already accounted for in seconds.
            t0 = time.monotonic()
            try:
                self.page.goto(BASE, timeout=45000, wait_until="domcontentloaded")
                self._human_scroll()
                self._pause()
            except Exception:
                pass
            logging.debug("session warmup took %.1fs", time.monotonic() - t0)

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
            logging.debug("long human-like pause: %.0fs", d)
            time.sleep(d)
        else:
            time.sleep(random.uniform(p["min_delay_s"], p["max_delay_s"]))

    def _human_scroll(self):
        for _ in range(random.randint(1, 3)):
            self.page.mouse.wheel(0, random.randint(200, 900))
            time.sleep(random.uniform(0.4, 1.6))

    # -- budget --
    def _budget_left(self):
        return self.budget is None or not self.budget.exhausted(self.navs)

    def _check_stop(self):
        if self.stop_requested:
            raise StopRun("interrupted (Ctrl-C)")
        if not self._budget_left():
            raise StopRun(self.budget.why_exhausted(self.navs))

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
            t0 = time.monotonic()
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
                load_s = time.monotonic() - t0
                t1 = time.monotonic()
                self._pause()
                wait_s = time.monotonic() - t1
                logging.debug("nav %d [%s] load=%.1fs wait=%.1fs html=%.0fkB %s",
                              self.navs, self.phase, load_s, wait_s,
                              len(content) / 1024, url)
                if load_s > 120:
                    logging.warning("nav %d took %.0fs — machine slept or Amazon "
                                    "stalled", self.navs, load_s)
                return content
            except PWTimeout:
                logging.warning("timeout after %.0fs on %s",
                                time.monotonic() - t0, url)
                self.new_session()
                continue
        raise StopRun("repeated failures on " + url)

    def _is_blocked(self, content):
        low = content[:6000].lower()
        return any(m in low for m in BLOCK_MARKERS)

    def _dismiss_cookie_banner(self):
        # count() is instant; clicking blind burned 2.5s per nav (~16% of a run)
        # waiting for a banner that only exists once per session.
        try:
            loc = self.page.locator("#sp-cc-accept")
            if loc.count():
                loc.first.click(timeout=2500)
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

        # 5. language
        if language and language.strip().lower() != "english":
            return "excluded_not_english", fields

        # 6. buyable new, right now
        buyable, price = self._buyability(content, body)
        fields["price"] = price
        if not buyable:
            return "excluded_unavailable", fields

        # 7. publisher final gate: ambiguous -> review, missing -> review, else match
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
        from datetime import datetime
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
