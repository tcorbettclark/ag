"""The two phases: enumerate listings into the backlog, then classify the backlog.

They are deliberately separate so they can become separate tools (or an
alternating batch loop) — see README, "Phases".
"""

import logging
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from . import works
from .constants import AUTHOR_PAGE, BASE, now_iso
from .errors import StopRun


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
    try:
        while page_no <= task.max_pages:
            if crawler.stop_requested:
                return crawler.navs - start_navs  # do NOT mark done; resume next run
            if not crawler._budget_left():
                logging.info("enumeration budget reached mid-task at %s page %d",
                             task.key, page_no)
                return crawler.navs - start_navs  # resume this task next run
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
    return crawler.navs - start_navs


def author_page_task(crawler, store, rescan, fresh_days):
    prog = store.progress("authorpage")
    if search_is_fresh(prog, rescan, fresh_days):
        logging.debug("skipping fresh-enough search authorpage")
        return 0
    logging.info("enumerating author page")
    start_navs = crawler.navs
    try:
        content = crawler.goto(AUTHOR_PAGE)
        asins = crawler.extract_asins(content)
        new = store.add_pending(asins, "authorpage")
        logging.info("  author page: %d new ASINs queued", new)
    finally:
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
    """Fetch and classify queued ASINs. Returns the number classified."""
    crawler.phase = "classify"
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
