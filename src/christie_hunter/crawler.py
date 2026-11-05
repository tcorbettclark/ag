"""The Playwright crawler: sessions, polite navigation, and page classification.

Navigation is defensive in one specific direction: a single bad *page* must
never end a run. Timeouts, dead ASINs, soft blocks and layout changes are all
recoverable per-item events (see `errors.PageFailure`); only a real captcha,
repeated enough times, stops the run.
"""

import logging
import random
import re
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout

from .constants import (ASIN_RE, AUTHOR_CONTAMINATION, BASE, BLOCK_MARKERS,
                        BLOCK_MAX_BYTES, CARD_NOISE, EXPECT_MARKERS,
                        HC_AMBIGUOUS, HC_DEFINITE, NON_BOOK_MARKERS,
                        SESSION_FILE, UA_TEMPLATE, VIEWPORTS)
from .errors import (Blocked, DeadPage, StopRun, Transient, UnexpectedPage)

# one JS round-trip for the whole results page, rather than one per card
_CARDS_JS = """() => Array.from(
    document.querySelectorAll('[data-component-type="s-search-result"]'))
  .slice(0, 100)
  .map(el => [el.getAttribute('data-asin') || '', el.innerText || ''])"""


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
        self.block_hits = 0
        self.cooldowns_used = 0
        pol = cfg.get("politeness", {})
        self.refresh_navs = self._refresh_in()      # 0 = never rotate on a timer
        self.until_refresh = self.refresh_navs
        self.max_cooldowns = pol.get("max_cooldowns_per_run", 3)
        self.cooldown_s = (pol.get("cooldown_min_s", 90),
                           pol.get("cooldown_max_s", 210))
        self.session_file = Path(cfg["output_dir"]) / SESSION_FILE
        self.ua = None
        self.stop_requested = False
        # authorised licensees (BBC audio, Laurence King puzzle books, ...) —
        # never rights violations, exclude outright like HC
        self.licensed = [re.compile(p, re.I)
                         for p in cfg.get("licensed_publishers", [])]

    def _refresh_in(self):
        p = self.cfg["politeness"]
        lo = p.get("session_refresh_min", 0)
        hi = p.get("session_refresh_max", 0)
        if not hi:
            return 0                       # cookie jar lives as long as the run
        return random.randint(lo, max(lo, hi))

    # -- sessions --
    def _launch(self, pw):
        """Prefer a real Chrome/Chromium build over the bundled headless shell.

        The headless shell leaks `navigator.plugins == 0`,
        `pdfViewerEnabled == false`, `window.chrome === undefined` and a
        SwiftShader WebGL renderer; the full browser in `--headless=new` leaks
        none of those.
        """
        args = ["--disable-blink-features=AutomationControlled"]
        wanted = self.cfg.get("browser_channel", "auto")
        for channel in ([wanted] if wanted not in (None, "", "auto")
                        else ["chrome", "chromium", None]):
            try:
                b = pw.chromium.launch(headless=True, args=args, channel=channel)
                logging.info("browser: %s (%s)",
                             channel or "bundled chromium", b.version)
                return b
            except Exception as e:
                logging.debug("cannot launch channel=%s: %s", channel, e)
        raise RuntimeError("no usable chromium/chrome build")

    def _detect_ua(self):
        """The browser's own UA, with the headless token removed.

        Hardcoding a UA (as this used to) guarantees a version string that
        disagrees with the browser's `userAgentData.brands` and TLS stack; the
        built-in one is right by construction.
        """
        major = (self.browser.version or "0").split(".")[0]
        fallback = UA_TEMPLATE.format(version=major)
        ctx = None
        try:
            ctx = self.browser.new_context()
            ua = ctx.new_page().evaluate("() => navigator.userAgent")
            return (ua or fallback).replace("HeadlessChrome", "Chrome")
        except Exception:
            return fallback
        finally:
            if ctx:
                try:
                    ctx.close()
                except Exception:
                    pass

    def start(self, pw):
        self.pw = pw
        self.browser = self._launch(pw)
        self.ua = self._detect_ua()
        logging.debug("user agent: %s", self.ua)
        self.new_session()

    def _save_cookies(self):
        if not self.context:
            return
        try:
            self.context.storage_state(path=str(self.session_file))
        except Exception:
            pass

    def new_session(self, warmup=True):
        self._save_cookies()
        if self.context:
            try:
                self.context.close()
            except Exception:
                pass
        vw = random.choice(VIEWPORTS)
        reuse = self.session_file if self.session_file.exists() else None
        self.context = self.browser.new_context(
            user_agent=self.ua, viewport={"width": vw[0], "height": vw[1]},
            locale="en-GB", timezone_id="Europe/London",
            storage_state=str(reuse) if reuse else None,
            screen={"width": vw[0], "height": vw[1]})
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
            self._save_cookies()
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

    def _maybe_rotate_session(self):
        if not self.refresh_navs:
            return
        self.until_refresh -= 1
        if self.until_refresh <= 0:
            logging.info("rotating session after %d navigations "
                         "(same cookie jar)", self.refresh_navs)
            self.new_session()
            self.until_refresh = self.refresh_navs

    # -- navigation with block detection --
    def goto(self, url, expect=None, referer=None):
        """Fetch one page. Returns its HTML.

        `expect` is "product" or "search": it turns "did we actually get the
        page we asked for?" into an explicit check, so a soft block or a layout
        change fails loudly instead of being parsed as if it were content.

        Raises DeadPage / Transient / Blocked / UnexpectedPage for single-page
        problems (the caller carries on) and StopRun when the run is over.
        """
        self._check_stop()
        self.navs += 1
        self._maybe_rotate_session()

        t0 = time.monotonic()
        kwargs = {"timeout": 45000, "wait_until": "domcontentloaded"}
        if referer:
            kwargs["referer"] = referer
        resp = None
        try:
            resp = self.page.goto(url, **kwargs)
        except PWTimeout:
            took = time.monotonic() - t0
            logging.warning("timeout after %.0fs on %s", took, url)
            self.new_session()
            raise Transient(f"timeout ({took:.0f}s)") from None
        except Exception as e:                       # net::ERR_*, crash, ...
            logging.warning("navigation failed on %s: %s", url, e)
            self.new_session()
            raise Transient(f"navigation failed: {e}") from None

        status = resp.status if resp else 200
        self._dismiss_cookie_banner()
        self._human_scroll()
        content = self.page.content()

        # a dead ASIN is not a block: it is a permanently gone product
        if status in (404, 410):
            logging.info("dead page (status %s) %s", status, url)
            raise DeadPage(f"status {status}")

        if status in (429, 503) or self._is_captcha(content):
            self._on_block(status, url)              # returns only if we may continue

        if expect and not self._looks_like(content, expect):
            if expect == "product" and self._is_non_book_page(content):
                # A film or merch page found by a book search is a *result*, not
                # a failure: parse_product files it as excluded_not_christie.
                # Treating it as a failed fetch is what stopped a run after 3
                # navs on three Prime Video listings.
                logging.info("not a book listing (status %s, %.0fkB): %s",
                             status, len(content) / 1024, url)
            else:
                logging.warning("unexpected %s page (status %s, %.0fkB): %s",
                                expect, status, len(content) / 1024, url)
                raise UnexpectedPage(f"not a {expect} page (status {status}, "
                                     f"{len(content) / 1024:.0f}kB)")

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

    def _on_block(self, status, url):
        """Cool down once and let the caller move on; stop only when it is
        clearly not a one-off.

        The old ladder slept 5/15/45 min and retried *the same URL* each time,
        so one blocked page cost an hour and then ended the run. Now a block
        costs one short, capped cooldown and the caller retries different work.
        """
        self.block_hits += 1
        logging.warning("block detected (status %s) on %s", status, url)
        if self.cooldowns_used >= self.max_cooldowns:
            raise StopRun(f"blocked {self.block_hits} times in a row "
                          f"(cooled down {self.cooldowns_used}x); "
                          "resumable, run again later")
        self.cooldowns_used += 1
        wait = random.uniform(*self.cooldown_s)
        logging.warning("cooling down %.0fs then starting a fresh session "
                        "(cooldown %d/%d)", wait, self.cooldowns_used,
                        self.max_cooldowns)
        time.sleep(wait)
        self.new_session()
        raise Blocked(f"blocked (status {status}); cooled down, "
                      "continuing with other work")

    def _is_captcha(self, content):
        if len(content) > BLOCK_MAX_BYTES:
            return False                # a full page, not an interstitial
        low = content[:20000].lower()
        return any(m in low for m in BLOCK_MARKERS)

    @staticmethod
    def _is_non_book_page(content):
        """A real Amazon page that can never be a book listing (e.g. Prime Video).

        Only consulted once the product-page markers are already missing, so a
        real book page cannot be diverted here — and only a page carrying one of
        these markers is exempted from the `expect` check, which keeps a layout
        change failing loudly instead of silently excluding everything.
        """
        low = content[:400000].lower()
        return any(m in low for m in NON_BOOK_MARKERS)

    @staticmethod
    def _looks_like(content, expect):
        low = content[:400000].lower()
        return any(m in low for m in EXPECT_MARKERS.get(expect, ()))

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

    def extract_cards(self):
        """[(asin, card_text)] for the search-result cards on the current page.

        Empty on pages that have no s-search-result cards (the author page, a
        product page), which is the caller's cue to fall back to a raw scan.
        """
        try:
            rows = self.page.evaluate(_CARDS_JS) or []
        except Exception as e:
            logging.debug("card extraction failed: %s", e)
            return []
        cards, seen = [], set()
        for asin, text in rows:
            if len(asin) != 10 or asin in seen:
                continue
            seen.add(asin)
            cards.append((asin, text or ""))
        return cards

    @staticmethod
    def _card_wanted(text):
        """Is this result card worth a product-page fetch?

        38% of the last run's navigations were pages that were never Christie
        books — sponsored cards and "customers also viewed" filler. A card that
        is rendered but never says "Christie" cannot be a Christie book, so it
        can be skipped without a fetch. Anything unrendered/short is kept: the
        cost of a wasted fetch is far lower than that of a missed match.
        """
        low = (text or "").lower()
        if len(low) < 20:
            return True
        if any(n in low for n in CARD_NOISE):
            return False
        return "christie" in low

    def discover(self, content):
        """ASINs on a listing page, pre-filtered.

        Returns (asins, dropped, n_cards). `n_cards == 0` on pages that have no
        result cards at all, which is how the caller tells "this search has
        run out of results" from "every card here was filtered out".
        """
        if not self.cfg.get("prefilter_cards", True):
            return self.extract_asins(content), 0, 0
        cards = self.extract_cards()
        if not cards:
            return self.extract_asins(content), 0, 0
        keep, dropped = [], 0
        for asin, text in cards:
            if self._card_wanted(text):
                keep.append(asin)
            else:
                dropped += 1
        return keep, dropped, len(cards)

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

        # 0. a real page that is not a book listing at all (Prime Video, merch).
        #    Not a Christie book, and worth naming so the record is readable.
        if not title and self._is_non_book_page(content):
            fields["title"] = self._page_heading()
            fields["note"] = "not a book listing"
            return "excluded_not_christie", fields

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

    def _page_heading(self):
        """<h1> (then <title>) for a page with no #productTitle."""
        for sel in ("h1", "title"):
            try:
                t = self.page.locator(sel).first.inner_text().strip()
                if t:
                    return re.sub(r"\s+", " ", t)
            except Exception:
                continue
        return ""

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
