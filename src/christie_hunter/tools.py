"""Maintenance commands that never touch the network (except `check`).

All of them are single-writer operations on state.json, so they refuse to run
while the crawler is active.
"""

import json
import logging
import re

from .constants import STATE_FILE
from .reports import write_reports
from .store import Store, require_idle


def reclassify_licensed(cfg):
    """Re-apply `licensed_publishers` to records already in state.json.

    Records excluded by a gate that runs *before* the licensed check (HC
    imprint, not-Christie) keep their label; everything else is re-decided, so
    a licensed French edition filed as excluded_not_english becomes
    excluded_licensed, matching the pipeline's order.
    """
    licensed = [re.compile(p, re.I) for p in cfg.get("licensed_publishers", [])]
    store = Store(STATE_FILE)
    changed = []
    for asin, rec in store.records.items():
        cls = rec.get("classification") or ""
        if cls in ("excluded_harpercollins", "excluded_not_christie"):
            continue
        publisher = rec.get("publisher")
        if not publisher:
            continue
        if any(p.search(publisher) for p in licensed):
            if cls == "excluded_licensed":
                continue  # already labelled; keep re-runs quiet
            changed.append((asin, cls, publisher))
            rec["classification"] = "excluded_licensed"
    if not changed:
        print("No records to re-classify.")
        return 0
    store.save()
    write_reports(store, cfg)
    for asin, old, publisher in changed:
        print(f"  {asin}  {old or '(none)':<20} -> excluded_licensed   [{publisher}]")
    print(f"\nRe-classified {len(changed)} records as excluded_licensed; "
          "reports rewritten.")
    return len(changed)


def rebuild_reports(cfg):
    """Regenerate output/*.csv from state.json alone (no network, no state change)."""
    store = Store(STATE_FILE)  # in_flight -> pending happens in memory only
    write_reports(store, cfg)
    print(f"rebuilt reports from {STATE_FILE} ({len(store.records)} classified "
          f"ASINs, {len(store.pending)} pending) into {cfg['output_dir']}/")
    return 0


def requeue_unavailable(cfg):
    """Put `excluded_unavailable` records back in the queue after a buy-box fix."""
    store = Store(STATE_FILE)
    records = store.records
    targets = [a for a, r in records.items()
               if r.get("classification") == "excluded_unavailable"]
    if not targets:
        print("No excluded_unavailable records to re-queue.")
        return 0
    for a in targets:
        del records[a]
        if a not in store.pending:
            store.pending.insert(0, a)
        store.data.setdefault("sources", {})[a] = "requeued_unavailable"
    store.save()
    print(f"Re-queued {len(targets)} ASINs: {targets}")
    print("They will be re-classified on the next run.")
    return len(targets)


def check_one(cfg, url):
    """Classify a single ASIN/URL with the tool's exact logic, with forensics."""
    import re as _re
    import time

    from playwright.sync_api import sync_playwright

    from .crawler import Crawler

    class NoSaveStore:
        """Stand-in for Store: same interface, but nothing is persisted."""
        data = {"sources": {}, "referers": {}, "attempts": {}}

        def save(self):
            pass

        @property
        def records(self):
            return {}

    crawler = Crawler(cfg, NoSaveStore())
    with sync_playwright() as pw:
        # exactly the same launch/fingerprint path a real run uses, so that
        # `check` tells you the truth about what Amazon serves this crawler
        crawler.start(pw)
        crawler.page.set_default_timeout(5000)

        t0 = time.time()
        crawler.page.goto(url, timeout=60000, wait_until="domcontentloaded")
        print(f"[{time.time()-t0:5.1f}s] page loaded: {crawler.page.url}")
        try:
            crawler.page.locator("#sp-cc-accept").click(timeout=2500)
        except Exception:
            pass

        m = _re.search(r"/dp/([A-Z0-9]{10})", crawler.page.url)
        asin = m.group(1) if m else "UNKNOWN"
        print(f"[{time.time()-t0:5.1f}s] ASIN: {asin}\n")

        content = crawler.page.content()
        print("--- buy-box HTML forensics ---")
        for marker in ["buy-now-button", "add-to-cart-button", "oneClickBuy",
                       "oneTimePrice", "corePrice_feature_div", "a-offscreen",
                       "kindle_buy", "buybox", "a-button-input", "newAccordion"]:
            n = content.count(marker)
            if n:
                print(f"  {marker!r}: {n} occurrence(s)")
                for hit in _re.finditer(r'.{40}' + _re.escape(marker) + r'.{60}', content):
                    print(f"      ...{hit.group(0)[:160]}...")
                    break
        prices = crawler.page.locator(".a-offscreen")
        print(f"  .a-offscreen elements: {prices.count()}")
        for i in range(min(4, prices.count())):
            print(f"    price[{i}]:", repr(prices.nth(i).inner_text()))

        classification, fields = crawler.parse_product(asin, content)
        print(f"[{time.time()-t0:5.1f}s] classification done\n")
        for k, v in fields.items():
            print(f"{k:>12}: {v}")
        print(f"\n>>> CLASSIFICATION: {classification}")

        body = crawler.page.locator("body").inner_text()
        print("\n--- availability signals ---")
        for phrase in ["Currently unavailable", "Buy now", "Kindle Price",
                       "Not yet published", "Read for Free", "unavailable",
                       "out of print", "You Save"]:
            if phrase.lower() in body.lower():
                print(f"  FOUND: {phrase!r}")
        for line in body.splitlines():
            if any(w in line.lower() for w in
                   ["unavailable", "kindle price", "not yet published"]):
                print("  line:", line.strip()[:120])
        crawler.close()
    return 0


def dump_summary(cfg):
    """Print what is in state.json right now (handy before/after a run)."""
    store = Store(STATE_FILE)
    counts = {}
    for rec in store.records.values():
        cls = rec.get("classification") or "(none)"
        counts[cls] = counts.get(cls, 0) + 1
    for cls, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"{n:6}  {cls}")
    print(f"{len(store.pending):6}  (pending classification)")
    runs = store.data.get("runs", [])
    if runs:
        print("\nlast run:", json.dumps(runs[-1]))
    return 0
