"""The two phases: enumerate listings into the backlog, then classify the backlog.

They are deliberately separate so they can become separate tools (or an
alternating batch loop) — see README, "Phases".

Neither phase may die on a single page. Every navigation failure is a
`PageFailure` (dead ASIN, transient, block, unexpected page) and is handled
here: requeue the item, leave search progress alone, carry on with other work.
Only `StopRun` (budget, Ctrl-C, a *repeated* block) ends a phase.
"""

import logging
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from . import works
from .constants import AUTHOR_PAGE, BASE, now_iso
from .errors import Blocked, DeadPage, StopRun, Transient, UnexpectedPage

MAX_ASIN_ATTEMPTS = 3


# --- URLs and tasks -----------------------------------------------------------

def search_url(index, query, page_no):
    return f"{BASE}/s?{urlencode({'k': query, 'i': index, 'page': page_no})}"


def author_search_url(index, page_no):
    rh = quote("p_27:Agatha Christie")
    return f"{BASE}/s?i={index}&rh={rh}&page={page_no}"


class Task:
    def __init__(self, key, url_fn, max_pages, source):
        self.key, self.url_fn, self.max_pages, self.source = key, url_fn, max_pages, source

    def __repr__(self):
        return f"Task({self.key})"


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
        for work, query in works.all_search_queries():
            tasks.append(Task(f"title|{idx}|{query}",
                              lambda p, q=query, i=idx: search_url(i, q, p),
                              s["title_max_pages"], f"title|{idx}|{work}"))
    return tasks


def search_is_fresh(prog, rescan, fresh_days):
    """True if this listing was enumerated recently enough to skip this run.

    Shared by the paginated searches and the author page so both honour
    `search_rescan_days`; `--rescan` overrides it for either.
    """
    if rescan or not prog["done"] or not prog["last_enumerated"]:
        return False
    last = datetime.fromisoformat(prog["last_enumerated"])
    return (datetime.now(timezone.utc) - last).days < fresh_days


# --- phase 1: enumeration -----------------------------------------------------

def _enumerate_page(crawler, store, task_key, url, source, referer):
    """Queue what one listing page offers. Returns (n_new, page_usable)."""
    try:
        content = crawler.goto(url, expect="search", referer=referer)
    except (Blocked, Transient, UnexpectedPage) as e:
        # leave next_page alone: a page we could not read must not be mistaken
        # for the end of the search (that used to silently retire it for a week)
        logging.warning("  %s: page not usable (%s) — leaving it for a later run",
                        task_key, e)
        return 0, False
    asins, dropped, n_cards = crawler.discover(content)
    if dropped:
        logging.info("  %s: %d of %d cards skipped as not-Christie/non-book",
                     task_key, dropped, n_cards)
    if not asins and n_cards:
        # every card on the page was filtered out: odd, but not the end of the
        # search — keep paging rather than retiring the search for a week
        logging.warning("  %s: every card on this page was filtered out", task_key)
        return 0, True
    if not asins:
        return 0, True          # a real "no results" page: the search is done
    new = store.add_pending([a for a in asins if not store.known(a)],
                            source, referer=url)
    return new, True


def run_task(crawler, store, task, rescan, fresh_days):
    """Enumerate one paginated search. Returns the number of navs used."""
    prog = store.progress(task.key)
    if search_is_fresh(prog, rescan, fresh_days):
        logging.debug("skipping fresh-enough search %s", task.key)
        return 0
    page_no = 1 if rescan else prog["next_page"]
    logging.info("enumerating %s from page %d", task.key, page_no)
    start_navs = crawler.navs
    completed = False
    # arrive at each page from the previous one, the way paging through results
    # actually looks; a resume after a gap still arrives from the homepage
    referer = f"{BASE}/"
    try:
        while page_no <= task.max_pages:
            if crawler.stop_requested:
                return crawler.navs - start_navs  # do NOT mark done; resume next run
            if not crawler._budget_left():
                logging.info("enumeration budget reached mid-task at %s page %d",
                             task.key, page_no)
                return crawler.navs - start_navs  # resume this task next run
            url = task.url_fn(page_no)
            new, usable = _enumerate_page(crawler, store, task.key, url,
                                          task.source, referer)
            referer = url
            if not usable:
                return crawler.navs - start_navs  # resume this task next run
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
    return crawler.navs - start_navs


def author_page_task(crawler, store, rescan, fresh_days):
    prog = store.progress("authorpage")
    if search_is_fresh(prog, rescan, fresh_days):
        logging.debug("skipping fresh-enough search authorpage")
        return 0
    logging.info("enumerating author page")
    start_navs = crawler.navs
    try:
        # no `expect`: a storefront is not a search-results page. Guard on size
        # instead — a soft block is a few kB, a real storefront is hundreds.
        content = crawler.goto(AUTHOR_PAGE, referer=BASE + "/")
    except (Blocked, Transient) as e:
        logging.warning("author page not usable (%s) — will re-read it next run", e)
        return crawler.navs - start_navs
    if len(content) < 50_000:
        logging.warning("author page suspiciously small (%.0fkB) — leaving it "
                        "for a later run", len(content) / 1024)
        return crawler.navs - start_navs
    # the author page is a shelf grid, not s-search-result cards: no pre-filter,
    # so fall back to a raw scan of every ASIN on the page
    asins = crawler.extract_asins(content)
    new = store.add_pending(asins, "authorpage", referer=AUTHOR_PAGE)
    logging.info("  author page: %d new ASINs queued", new)
    prog["done"] = True
    prog["last_enumerated"] = now_iso()
    store.save()
    return crawler.navs - start_navs


def search_phase(crawler, store, cfg, rescan, attempted):
    """Enumerate every listing that is not fresh. Marks each as attempted."""
    crawler.phase = "search"
    fresh_days = cfg["search_rescan_days"]
    navs = 0
    if "authorpage" not in attempted:
        attempted.add("authorpage")
        navs += author_page_task(crawler, store, rescan, fresh_days)
    for task in build_tasks(cfg):
        if crawler.stop_requested or not crawler._budget_left():
            break
        if task.key in attempted:
            continue
        attempted.add(task.key)
        navs += run_task(crawler, store, task, rescan, fresh_days)
    return navs


# --- phase 2: classification --------------------------------------------------

def classify_pending(crawler, store):
    """Fetch and classify queued ASINs. Returns the number classified.

    Each ASIN gets at most one attempt per pass: anything we could not read is
    put back on the queue at the *end* of the pass, so a bad page can neither
    stall the queue nor be retried in a tight loop.
    """
    crawler.phase = "classify"
    max_attempts = crawler.cfg.get("max_asin_attempts", MAX_ASIN_ATTEMPTS)
    streak_limit = crawler.cfg.get("max_failure_streak", 3)
    n = 0
    streak = 0
    deferred = []
    try:
        while True:
            crawler._check_stop()
            asin = store.take_pending()
            if not asin:
                break
            if asin in store.records:
                continue
            url = f"{BASE}/dp/{asin}"
            try:
                content = crawler.goto(url, expect="product",
                                       referer=store.referer(asin))
            except DeadPage as e:
                # a removed product is a finding, not a failure, never a block
                logging.info("  %s -> DEAD (%s)", asin, e)
                store.record(asin, classification="excluded_dead_asin", url=url,
                             source=store.data["sources"].get(asin, ""),
                             note=str(e))
                continue
            except (Blocked, Transient, UnexpectedPage) as e:
                streak += 1
                if streak >= streak_limit:
                    # three in a row with nothing working: this is the site, not
                    # the ASINs. Stop rather than flag the whole backlog.
                    raise StopRun(f"{streak} pages in a row failed ({e}); "
                                  "looks site-wide, resumable") from None
                attempts = store.note_failure(asin, str(e))
                if attempts >= max_attempts:
                    logging.warning("  %s -> gave up after %d attempts (%s)",
                                    asin, attempts, e)
                    store.record(asin, classification="unclear_fetch_failed",
                                 url=url, note=f"{attempts} attempts: {e}",
                                 source=store.data["sources"].get(asin, ""))
                else:
                    logging.info("  %s: %s — deferred (attempt %d/%d)",
                                 asin, e, attempts, max_attempts)
                    deferred.append(asin)
                continue
            streak = 0
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
    finally:
        for asin in deferred:
            store.requeue(asin)
    return n
