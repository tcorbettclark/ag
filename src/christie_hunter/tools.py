"""Maintenance commands. Only `check` and `verify-matches` touch the network.

All of them are single-writer operations on state.json, so they refuse to run
while the crawler is active.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from .constants import BASE, STATE_FILE, now_iso
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
        crawler.phase = "check"

        t0 = time.time()
        # raw goto on purpose: `check` must show whatever Amazon served, not
        # raise DeadPage/Blocked like the crawl path does. Log it by hand so it
        # still lands in run.log with the other per-nav timings.
        got = crawler.page.goto(url, timeout=60000, wait_until="domcontentloaded")
        logging.debug("nav 1 [check] load=%.1fs status=%s html=%.0fkB %s",
                      time.time() - t0, got.status if got else "?",
                      len(crawler.page.content()) / 1024, crawler.page.url)
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
    verified = [r for r in store.records.values() if r.get("verified_at")]
    if verified:
        gone = sum(1 for r in verified if r.get("verify_result") == "gone")
        print(f"{len(verified):6}  (verified: {len(verified) - gone} still live, "
              f"{gone} gone)")
    runs = store.data.get("runs", [])
    if runs:
        print("\nlast run:", json.dumps(runs[-1]))
    return 0


# --- verify-matches ----------------------------------------------------------

def matches_to_verify(store, since_days=0.0, include_unclear=False, only=None):
    """Which records to re-check, oldest-verified first.

    ASINs vanish from Amazon without warning (a self-published Kindle edition
    can be there one night and a 404 the next), so a `match` is a claim about
    the past until it has been verified recently.
    """
    def wanted(rec):
        cls = rec.get("classification") or ""
        if cls == "match":
            return True
        return include_unclear and cls.startswith("unclear")

    cutoff = None
    if since_days and since_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

    def last_verified(rec):
        try:
            return datetime.fromisoformat(rec["verified_at"])
        except (KeyError, TypeError, ValueError):
            return None

    if only:
        return [a for a in only if a in store.records]

    keep = []
    for asin, rec in store.records.items():
        if not wanted(rec):
            continue
        seen = last_verified(rec)
        if cutoff and seen and seen > cutoff:
            continue
        keep.append((0 if seen is None else 1,
                 seen or datetime.min.replace(tzinfo=timezone.utc), asin))
    # never-verified first, then oldest verification first
    return [asin for _, _, asin in sorted(keep)]


def apply_verification(store, asin, verdict, fields=None, note=""):
    """Fold a fresh classification into an existing record.

    The classification becomes the *current* verdict — that is what makes
    matches.csv mean "a match right now" — but the old one is kept in
    `previous_classification`, and `verified_at` / `verify_result` record that
    (and when) the record was re-checked. Evidence is never touched: for a
    listing that has since gone, the old screenshot is the only proof left.
    """
    rec = store.records[asin]
    old = rec.get("classification") or ""
    update = dict(fields or {})
    update["price"] = update.get("price")   # a price is only current if re-seen
    update["classification"] = verdict
    update["verified_at"] = now_iso()
    if verdict != old:
        update["previous_classification"] = old
    if verdict == "match":
        update["verify_result"] = "live"
    elif verdict == "excluded_dead_asin":
        update["verify_result"] = "gone"
    else:
        update["verify_result"] = "changed"
    if note:
        update["verify_note"] = note
    store.record(asin, **update)
    return old, verdict


def verify_matches(cfg, hours=None, since_days=0.0, include_unclear=False,
                   limit=None, only=None):
    """Re-fetch every match and re-decide it. Needs the network."""
    import time

    from playwright.sync_api import sync_playwright

    from .budget import RunBudget
    from .crawler import Crawler
    from .errors import (Blocked, DeadPage, StopRun, Transient, UnexpectedPage)

    store = Store(STATE_FILE)
    targets = matches_to_verify(store, since_days, include_unclear, only=only)
    if limit:
        targets = targets[:limit]
    if not targets:
        print("Nothing to verify (no matches, or all verified within --since).")
        return 0

    budget = RunBudget.start(hours if hours is not None else cfg["run_hours"],
                             cfg["max_navs_per_run"])
    print(f"verifying {len(targets)} record(s) — budget {budget.describe()}")
    crawler = Crawler(cfg, store)
    crawler.phase = "verify"
    crawler.budget = budget
    counts = {"live": 0, "unchanged": 0, "changed": 0, "gone": 0, "unverified": 0}
    changes, gone = [], []
    t0 = time.monotonic()
    try:
        with sync_playwright() as pw:
            crawler.start(pw)
            try:
                for i, asin in enumerate(targets, 1):
                    if not crawler._budget_left():
                        print(f"budget reached after {i - 1} of {len(targets)} "
                              "— re-run to continue")
                        break
                    url = f"{BASE}/dp/{asin}"
                    tag = f"[{i}/{len(targets)}] {asin}"
                    try:
                        content = crawler.goto(url, expect="product",
                                               referer=store.referer(asin))
                    except DeadPage as e:
                        old, _ = apply_verification(store, asin,
                                                    "excluded_dead_asin", note=str(e))
                        counts["gone"] += 1
                        gone.append((asin, old))
                        print(f"  {tag}  GONE (was {old}) — {e}")
                        continue
                    except (Blocked, Transient, UnexpectedPage) as e:
                        counts["unverified"] += 1
                        print(f"  {tag}  could not verify ({e}) — left as it was")
                        continue
                    old = store.records[asin].get("classification") or ""
                    verdict, fields = crawler.parse_product(asin, content)
                    apply_verification(store, asin, verdict, fields)
                    if verdict == old:
                        counts["live" if verdict == "match" else "unchanged"] += 1
                        print(f"  {tag}  still {verdict}")
                    else:
                        counts["changed"] += 1
                        changes.append((asin, old, verdict, fields.get("publisher")))
                        print(f"  {tag}  {old} -> {verdict} "
                              f"({fields.get('publisher')})")
            except StopRun as e:
                print(f"stopped: {e}")
            finally:
                crawler.close()
    finally:
        store.save()
        write_reports(store, cfg)

    print(f"\nverified {sum(counts.values())} record(s) in "
          f"{time.monotonic() - t0:.0f}s / {crawler.navs} navs")
    print(f"  {counts['live']:6}  still a match")
    if counts["unchanged"]:
        print(f"  {counts['unchanged']:6}  unchanged (not a match)")
    if counts["changed"]:
        print(f"  {counts['changed']:6}  changed verdict")
        for asin, old, new, publisher in changes:
            print(f"          {asin}  {old} -> {new}  [{publisher}]")
    if counts["gone"]:
        print(f"  {counts['gone']:6}  gone from Amazon (kept, with their evidence)")
        for asin, old in gone:
            print(f"          {asin}  was {old}")
    if counts["unverified"]:
        print(f"  {counts['unverified']:6}  could not be checked this time")
    print("reports rewritten; `rebuild-reports` re-derives them offline")
    return 0
