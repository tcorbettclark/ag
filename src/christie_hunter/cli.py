"""Command line: `christie-hunter [run|reclassify|rebuild-reports|requeue-unavailable|check|summary]`."""

import argparse
import logging
import random
import signal
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import __version__, config as config_mod, excel, tools
from .budget import RunBudget, format_hours, parse_duration
from .constants import CONFIG_FILE, now_iso
from .crawler import Crawler
from .errors import StopRun
from .reports import write_reports
from .search import build_tasks, classify_pending, search_phase
from .store import (Store, active_run_pid, clear_lock, require_idle,
                    write_lock)

COMMANDS = ("run", "reclassify", "rebuild-reports", "requeue-unavailable",
            "verify-matches", "check", "export-excel", "summary")


# --- the run ------------------------------------------------------------------
# --- the alternating loop -------------------------------------------------

def _crawl(crawler, store, cfg, budget, rescan, classify_only, search_only):
    """Enumerate, then classify, until the budget or the work runs out."""
    attempted = set()   # searches this run has already tried (never retry)
    reason = "completed"
    while True:
        start_navs = crawler.navs
        if not classify_only:
            # the enumeration phase gets its share of the budget only
            crawler.budget = budget.scaled(cfg["search_share"])
            search_phase(crawler, store, cfg, rescan, attempted)
        if not search_only:
            crawler.budget = budget
            classify_pending(crawler, store)
        if crawler.stop_requested:
            raise StopRun("interrupted (Ctrl-C)")

        worked = crawler.navs > start_navs
        pending = 0 if search_only else len(store.pending)
        stale = [] if classify_only else [
            t.key for t in build_tasks(cfg) if t.key not in attempted]
        if not pending and not stale:
            reason = "nothing left to do"
            break
        if not worked:
            reason = "no progress possible this pass"
            break
        if not crawler._budget_left():
            break
    return reason


def run(cfg, hours=None, rescan=False, classify_only=False, search_only=False):
    """One run: enumerate what is stale, then classify the backlog, until the
    budget or the work runs out."""
    run_hours = hours if hours is not None else cfg["run_hours"]
    budget = RunBudget.start(run_hours, cfg["max_navs_per_run"])
    store = Store()

    Path(cfg["output_dir"]).mkdir(exist_ok=True)
    logging.info("state: %d classified, %d pending classification",
                 len(store.records), len(store.pending))
    logging.info("run budget: %s", budget.describe())

    crawler = Crawler(cfg, store)

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
            try:
                reason = _crawl(crawler, store, cfg, budget, rescan,
                                classify_only, search_only)
            finally:
                # must run *before* Playwright tears down, or the cookie jar
                # cannot be saved
                crawler.close()
    except StopRun as e:
        reason = str(e)
        logging.warning("run stopped: %s", reason)
    finally:
        store.data["runs"].append({
            "started": run_start, "ended": now_iso(),
            "budget_hours": run_hours, "planned_end": budget.deadline.isoformat(),
            "navs": crawler.navs, "end_reason": reason})
        store.save()
        write_reports(store, cfg)
        n_match = sum(1 for r in store.records.values()
                      if r.get("classification") == "match")
        n_unclear = sum(1 for r in store.records.values()
                        if (r.get("classification") or "").startswith("unclear"))
        logging.info(
            "run done after %s / %d navs (%s) | total: %d records, %d matches, "
            "%d unclear, %d still pending. Re-run to resume.",
            format_hours(budget.elapsed_h()), crawler.navs, reason,
            len(store.records), n_match, n_unclear, len(store.pending))
    return 0


# --- cli ----------------------------------------------------------------------

def setup_logging(cfg):
    """One run.log for every command that touches the network.

    The file keeps per-nav timings at DEBUG (`nav 42 [verify] load=3.1s ...`);
    the console stays readable at INFO. `verify-matches` and `check` used to
    log nowhere at all, which is why there were no timings for them.
    """
    out = Path(cfg["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(out / "run.log"),
                  logging.StreamHandler(sys.stdout)])
    for h in logging.getLogger().handlers[1:]:
        h.setLevel(logging.INFO)
    return logging.getLogger()


def _duration(args):
    """--for, parsed into hours; None means "use run_hours from config"."""
    if not getattr(args, "duration", None):
        return None
    try:
        return parse_duration(args.duration)
    except ValueError as e:
        raise SystemExit(f"error: {e}") from None


def build_parser():
    ap = argparse.ArgumentParser(
        prog="christie-hunter",
        description="Find English-language Agatha Christie books on amazon.co.uk "
                    "that are not published by HarperCollins or a known licensee.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("run", help="crawl (default)")
    p.add_argument("--for", dest="duration", metavar="DURATION",
                   help="how long to run for, e.g. 8h, 90m, 45s "
                        "(default: run_hours in config.json)")
    p.add_argument("--rescan", action="store_true",
                   help="force re-enumeration of completed searches and the author page")
    p.add_argument("--classify-only", action="store_true", help="skip enumeration")
    p.add_argument("--search-only", action="store_true", help="skip classification")

    sub.add_parser("reclassify",
                   help="re-apply licensed_publishers to existing records")
    sub.add_parser("rebuild-reports", help="regenerate output/*.csv from state.json")
    sub.add_parser("requeue-unavailable",
                   help="re-queue excluded_unavailable records")
    pv = sub.add_parser("verify-matches",
                        help="re-fetch every match and re-decide it")
    pv.add_argument("--for", dest="duration", metavar="DURATION",
                    help="stop after this long (default: run_hours in config.json)")
    pv.add_argument("--since", dest="since_days", type=float, default=0.0,
                    metavar="DAYS",
                    help="skip records verified in the last DAYS (default: all)")
    pv.add_argument("--include-unclear", action="store_true",
                    help="also re-check unclear_* records")
    pv.add_argument("--limit", type=int, metavar="N",
                    help="check at most N records")
    pv.add_argument("--asin", action="append", metavar="ASIN",
                    help="verify only this record (repeatable)")
    pe = sub.add_parser("export-excel",
                        help="write the review workbook for a non-technical reader")
    pe.add_argument("--path", metavar="PATH",
                    help=f"where to write it (default: output/{excel.WORKBOOK_NAME})")
    sub.add_parser("summary", help="print classification counts from state.json")
    pc = sub.add_parser("check", help="classify one ASIN/URL with forensics")
    pc.add_argument("url", nargs="?", default="https://www.amazon.co.uk/dp/B0HJX3WB9T")
    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help")):
        argv.insert(0, "run")  # bare `christie-hunter` = a run
    args = build_parser().parse_args(argv)

    cfg = config_mod.load(CONFIG_FILE)

    if args.command in ("run", "verify-matches", "check"):
        setup_logging(cfg)

    if args.command == "run":
        hours = _duration(args)
        require_idle(cfg)
        write_lock(cfg)
        try:
            return run(cfg, hours=hours, rescan=args.rescan,
                       classify_only=args.classify_only,
                       search_only=args.search_only)
        finally:
            clear_lock(cfg)

    require_idle(cfg)
    if args.command == "reclassify":
        return tools.reclassify_licensed(cfg)
    if args.command == "rebuild-reports":
        return tools.rebuild_reports(cfg)
    if args.command == "requeue-unavailable":
        return tools.requeue_unavailable(cfg)
    if args.command == "verify-matches":
        # it writes state.json, so it holds the single-writer lock like a run
        write_lock(cfg)
        try:
            return tools.verify_matches(
                cfg, hours=_duration(args), since_days=args.since_days,
                include_unclear=args.include_unclear, limit=args.limit,
                only=args.asin)
        finally:
            clear_lock(cfg)
    if args.command == "export-excel":
        excel.export(cfg, args.path)
        return 0
    if args.command == "summary":
        return tools.dump_summary(cfg)
    if args.command == "check":
        return tools.check_one(cfg, args.url)
    raise SystemExit(f"unknown command {args.command!r}")
