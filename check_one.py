"""One-off: classify a single ASIN/URL with the tool's exact logic.
Does NOT read or write state.json, so it can run alongside the crawler."""

import json
import re
import sys

from playwright.sync_api import sync_playwright
from christie_hunter import Crawler, USER_AGENTS


class NoSaveStore:
    """Stand-in for Store: same interface, but nothing is persisted."""
    data = {"sources": {}}

    def save(self):
        pass

    @property
    def records(self):
        return {}


URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.amazon.co.uk/dp/B0HJX3WB9T"

cfg = json.load(open("config.json"))
crawler = Crawler(cfg, NoSaveStore())

with sync_playwright() as pw:
    crawler.browser = pw.chromium.launch(
        headless=True, args=["--disable-blink-features=AutomationControlled"])
    crawler.context = crawler.browser.new_context(
        user_agent=USER_AGENTS[0], viewport={"width": 1440, "height": 900},
        locale="en-GB", timezone_id="Europe/London")
    crawler.context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    crawler.page = crawler.context.new_page()
    crawler.page.set_default_timeout(5000)  # cap Playwright's hidden 30s auto-waits

    import time
    t0 = time.time()
    crawler.page.goto(URL, timeout=60000, wait_until="domcontentloaded")
    print(f"[{time.time()-t0:5.1f}s] page loaded: {crawler.page.url}")
    try:
        crawler.page.locator("#sp-cc-accept").click(timeout=2500)
    except Exception:
        pass

    m = re.search(r"/dp/([A-Z0-9]{10})", crawler.page.url)
    asin = m.group(1) if m else "UNKNOWN"
    print(f"[{time.time()-t0:5.1f}s] ASIN: {asin}\n")

    content = crawler.page.content()
    print("\n--- buy-box HTML forensics ---")
    import re as _re
    for marker in ["buy-now-button", "add-to-cart-button", "oneClickBuy", "oneTimePrice",
                   "corePrice_feature_div", "a-offscreen", "kindle_buy", "buybox",
                   "a-button-input", "newAccordion"]:
        n = content.count(marker)
        if n:
            print(f"  {marker!r}: {n} occurrence(s)")
            for m in _re.finditer(r'.{40}' + _re.escape(marker) + r'.{60}', content):
                print(f"      ...{m.group(0)[:160]}...")
                break
    # any visible .a-price at all?
    prices = crawler.page.locator(".a-offscreen")
    print(f"  .a-offscreen elements: {prices.count()}")
    for i in range(min(4, prices.count())):
        print(f"    price[{i}]:", repr(prices.nth(i).inner_text()))

    classification, fields = crawler.parse_product(asin, content)
    print(f"[{time.time()-t0:5.1f}s] classification done\n")
    for k, v in fields.items():
        print(f"{k:>12}: {v}")
    print(f"\n>>> CLASSIFICATION: {classification}")

    # --- debug: why did buyability fail? ---
    body = crawler.page.locator("body").inner_text()
    print("\n--- availability signals ---")
    for phrase in ["Currently unavailable", "Buy now", "Kindle Price", "Not yet published",
                   "Read for Free", "unavailable", "out of print", "You Save"]:
        if phrase.lower() in body.lower():
            print(f"  FOUND: {phrase!r}")
    for line in body.splitlines():
        if any(w in line.lower() for w in ["unavailable", "kindle price", "not yet published"]):
            print("  line:", line.strip()[:120])
    crawler.browser.close()