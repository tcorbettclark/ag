"""The CSV views. Pure derived data: everything here can be rebuilt from state.json."""

import csv
import logging
from pathlib import Path

COLUMNS = ["asin", "classification", "verified_at", "verify_result", "title",
           "author_line", "format", "publisher", "language", "price", "url",
           "source", "first_seen", "last_seen", "previous_classification",
           "note", "evidence"]


def write_reports(store, cfg):
    out = Path(cfg["output_dir"])
    out.mkdir(exist_ok=True)

    def rows(cls_pred):
        for asin, rec in store.records.items():
            if cls_pred(rec.get("classification")):
                yield {**rec, "asin": asin}

    def write_csv(path, pred):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
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
