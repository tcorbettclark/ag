"""The review workbook: one .xlsx to hand to a non-technical reader.

Built from state.json (the source of truth), never from the CSVs, so it keeps
working if the CSV reports are dropped. Offline and read-only: it refuses to run
while a crawl is active, and it never writes state.

It is a *living document*. Only the "Reviewer notes" column is editable in any
meaningful sense: those notes are read back from the previous export and
rewritten, keyed by ASIN, so they survive a re-export — including for a listing
which has since dropped out of the report. Everything else is regenerated.

Evidence (the PNG/HTML screenshots) is deliberately not in here; it stays in
output/evidence for the technical user. The workbook links to the live Amazon
page instead.
"""

import logging
import math
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .constants import STATE_FILE
from .store import Store

WORKBOOK_NAME = "christie-review.xlsx"
STATE_SHEET = "_state"           # hidden: asin -> reviewer notes (round-trip)
NOTES_SHEETS = ("Matches", "Needs review", "Could not read", "Gone or changed")
NOTES_HEADER = "Reviewer notes"

# which sheet a record belongs on
REVIEW_CLASSES = ("unclear_author", "unclear_publisher", "unclear_no_publisher",
                  "unclear_fetch_failed")
UNREADABLE_CLASSES = ("unclear_parse_fail",)

# short label in the sheet (it repeats on every row), long meaning in the
# Summary legend (it needs saying only once)
WHY_REVIEW_SHORT = {
    "unclear_author": "Author credit unclear",
    "unclear_publisher": "Publisher ambiguous",
    "unclear_no_publisher": "No publisher shown",
    "unclear_fetch_failed": "Page unreadable",
}

WHY_REVIEW = {
    "unclear_author": "Christie is credited, but not clearly as the author "
                      "(foreword, adaptation, or a co-author)",
    "unclear_publisher": "Publisher name is ambiguous (e.g. a bare “Harper” "
                         "or “Avon”)",
    "unclear_no_publisher": "No publisher shown on the page",
    "unclear_fetch_failed": "The page could not be read, even after retries",
}

WHY_GONE = {
    "excluded_dead_asin": "Listing removed from Amazon",
    "excluded_unavailable": "Not currently available to buy",
    "excluded_harpercollins": "Now a HarperCollins edition",
    "excluded_licensed": "Now a known licensee’s edition",
    "excluded_not_christie": "No longer a Christie book page",
    "excluded_not_english": "No longer an English edition",
}

# --- styling ------------------------------------------------------------------

INK = "1F3864"                   # header fill, near-black navy
BAND = "F2F5FA"                  # alternate row tint
RULE = "D9D9D9"
LINK = "0563C1"

HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
HEADER_FILL = PatternFill("solid", fgColor=INK)
BAND_FILL = PatternFill("solid", fgColor=BAND)
LINK_FONT = Font(color=LINK, underline="single")
THIN = Side(style="thin", color=RULE)
GRID = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
TOP = Alignment(vertical="top")
TOP_WRAP = Alignment(vertical="top", wrap_text=True)
HDR_ALIGN = Alignment(vertical="center", wrap_text=True)


# --- value cleaning -----------------------------------------------------------

def clean_author(author_line):
    """'by Agatha Christie (Author)  Format: Kindle Edition' -> 'Agatha Christie'."""
    if not author_line:
        return ""
    s = re.split(r"\bFormat:", author_line, maxsplit=1)[0]
    s = re.sub(r"^\s*by\s+", "", s, flags=re.I)
    s = re.sub(r"\((?:author|editor|foreword|introduction)\)", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+([,;])", r"\1", s)
    return s.strip(" ,;")


def primary_author(author_line):
    """Just the first credit.

    The Matches sheet only ever holds books where Christie is the primary,
    uncontaminated author, so the rest of the byline ("…, Søren Elung Jensen
    (Narrator), & 1 more") is noise that only makes the row taller. The Needs
    review sheet keeps the full string — there, the co-credits are the point.
    """
    first = re.split(r",|\band\b|&", clean_author(author_line))[0]
    return first.strip() or clean_author(author_line)


def clean_title(title):
    return re.sub(r"\s+", " ", (title or "")).strip()


def price_value(price):
    """'£3.99' -> 3.99, anything unreadable -> None."""
    if not price:
        return None
    m = re.search(r"(\d[\d,]*)(?:\.(\d{1,2}))?", str(price))
    if not m:
        return None
    whole = m.group(1).replace(",", "")
    frac = m.group(2) or "0"
    try:
        return float(f"{whole}.{frac}")
    except ValueError:
        return None


def as_date(value):
    """ISO timestamp -> naive local datetime (openpyxl rejects timezones)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def checked_on(rec):
    """When the listing was last actually looked at."""
    return as_date(rec.get("verified_at") or rec.get("last_seen"))


def amazon_url(asin, rec):
    return rec.get("url") or f"https://www.amazon.co.uk/dp/{asin}"


# --- routing ------------------------------------------------------------------

def route(records):
    """Split records into the workbook's sheets, each sorted for reading.

    Matches are grouped by publisher — that is the question a rights person is
    actually asking — then by title. The recipient can re-sort from there.
    """
    matches, review, unreadable, gone = [], [], [], []
    for asin, rec in records.items():
        cls = rec.get("classification") or ""
        if cls == "match":
            matches.append((asin, rec))
        elif cls in REVIEW_CLASSES:
            review.append((asin, rec))
        elif cls in UNREADABLE_CLASSES:
            unreadable.append((asin, rec))
        elif rec.get("previous_classification") == "match":
            gone.append((asin, rec))

    def by_publisher(item):
        _, rec = item
        return ((rec.get("publisher") or "\uffff").lower(),
                clean_title(rec.get("title")).lower())

    matches.sort(key=by_publisher)
    review.sort(key=lambda i: (i[1].get("classification") or "",
                               *by_publisher(i)))
    unreadable.sort(key=lambda i: (checked_on(i[1]) or datetime.min, i[0]))
    gone.sort(key=lambda i: (checked_on(i[1]) or datetime.min, i[0]), reverse=True)
    return matches, review, unreadable, gone


# --- sheet rendering ----------------------------------------------------------

def _header_row(ws, columns):
    for j, (title, width, _kind) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=j, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HDR_ALIGN
        cell.border = GRID
        ws.column_dimensions[get_column_letter(j)].width = width
    ws.row_dimensions[1].height = 30


def _render(ws, columns, rows, *, freeze="B2", autofilter=True):
    """rows: list of (values, url) where values aligns with `columns`."""
    _header_row(ws, columns)
    last_col = get_column_letter(len(columns))

    if not rows:
        cell = ws.cell(row=2, column=1, value="Nothing in this category right now.")
        cell.font = Font(italic=True, color="7F7F7F")
        ws.freeze_panes = freeze
        ws.sheet_view.showGridLines = False
        return

    for i, (values, url) in enumerate(rows, start=2):
        # rows must be tall enough for the wrapped text: Excel will not grow a
        # row to fit a wrapped cell on its own once the height is fixed, and a
        # clipped title is useless to a non-technical reader
        lines = 1
        for j, (_title, width, kind) in enumerate(columns, start=1):
            text = values[j - 1]
            if kind == "wrap" and text:
                chars = max(8, (width - 1) * 1.15)   # proportional font
                lines = max(lines, math.ceil(len(str(text)) / chars))
        ws.row_dimensions[i].height = min(120, max(15, 14.5 * lines))

        banded = i % 2 == 0
        for j, value in enumerate(values, start=1):
            kind = columns[j - 1][2]
            cell = ws.cell(row=i, column=j)
            cell.border = GRID
            if kind == "date":
                cell.value = value
                if isinstance(value, datetime):
                    cell.number_format = "dd mmm yyyy"
                cell.alignment = Alignment(vertical="top", horizontal="left")
            elif kind == "money":
                cell.value = value
                cell.number_format = '£#,##0.00'
                cell.alignment = Alignment(vertical="top", horizontal="right")
            elif kind == "link":
                cell.value = "Open on Amazon"
                if url:
                    cell.hyperlink = url
                    cell.font = LINK_FONT
                cell.alignment = TOP
            elif kind == "wrap":
                cell.value = value or None
                cell.alignment = TOP_WRAP
            elif kind == "asin":
                cell.value = value or None
                cell.font = Font(name="Menlo", size=10, color="595959")
                cell.alignment = TOP
            else:
                cell.value = value or None
                cell.alignment = TOP
            if banded and kind != "link":
                cell.fill = BAND_FILL

    ws.freeze_panes = freeze
    ws.sheet_view.showGridLines = False
    if autofilter:
        ws.auto_filter.ref = f"A1:{last_col}{len(rows) + 1}"


def _sheet_matches(ws, rows, notes):
    columns = [
        ("Title", 50, "wrap"), ("Author", 28, "wrap"), ("Publisher", 32, "wrap"),
        ("Format", 17, "text"), ("Checked on", 12, "date"),
        ("Amazon listing", 15, "link"), ("ASIN", 12, "asin"),
        ("Price", 9, "money"), (NOTES_HEADER, 34, "wrap"),
    ]
    out = [([clean_title(rec.get("title")), primary_author(rec.get("author_line")),
             rec.get("publisher") or "—", rec.get("format") or "—",
             checked_on(rec), None, asin, price_value(rec.get("price")),
             notes.get(asin, {}).get("notes", "")],
            amazon_url(asin, rec))
           for asin, rec in rows]
    _render(ws, columns, out)


def _sheet_review(ws, rows, notes):
    columns = [
        ("Title", 44, "wrap"), ("Author", 26, "wrap"), ("Publisher", 28, "wrap"),
        ("Format", 16, "text"), ("Checked on", 12, "date"),
        ("Why it needs review", 40, "wrap"), ("Amazon listing", 15, "link"),
        ("ASIN", 12, "asin"), (NOTES_HEADER, 32, "wrap"),
    ]
    out = [([clean_title(rec.get("title")), clean_author(rec.get("author_line")),
             rec.get("publisher") or "—", rec.get("format") or "—",
             checked_on(rec), WHY_REVIEW_SHORT.get(rec.get("classification"), ""),
             None, asin, notes.get(asin, {}).get("notes", "")],
            amazon_url(asin, rec))
           for asin, rec in rows]
    _render(ws, columns, out)


def _sheet_unreadable(ws, rows, notes):
    columns = [
        ("ASIN", 12, "asin"), ("Amazon listing", 15, "link"),
        ("Last seen", 12, "date"), (NOTES_HEADER, 48, "wrap"),
    ]
    out = [([asin, None, checked_on(rec), notes.get(asin, {}).get("notes", "")],
            amazon_url(asin, rec))
           for asin, rec in rows]
    _render(ws, columns, out)


def _sheet_gone(ws, rows, notes):
    columns = [
        ("Title", 44, "wrap"), ("Author", 26, "wrap"), ("Publisher", 28, "wrap"),
        ("Format", 16, "text"), ("Was a match until", 15, "date"),
        ("What happened", 30, "wrap"), ("Amazon listing", 15, "link"),
        ("ASIN", 12, "asin"), (NOTES_HEADER, 32, "wrap"),
    ]
    out = [([clean_title(rec.get("title")), primary_author(rec.get("author_line")),
             rec.get("publisher") or "—", rec.get("format") or "—",
             checked_on(rec),
             WHY_GONE.get(rec.get("classification"), "No longer a match"),
             None, asin, notes.get(asin, {}).get("notes", "")],
            amazon_url(asin, rec))
           for asin, rec in rows]
    _render(ws, columns, out)


def _sheet_summary(ws, counts, publishers, generated):
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 10
    ws.column_dimensions["C"].width = 76

    ws["A1"] = "Agatha Christie on amazon.co.uk"
    ws["A1"].font = Font(bold=True, size=18, color=INK)
    ws["A2"] = "English-language editions that are not HarperCollins or a known licensee"
    ws["A2"].font = Font(italic=True, size=11, color="595959")

    meta = [
        ("Generated", generated.strftime("%d %B %Y, %H:%M")),
        ("Source", "state.json (the hunter's own records)"),
        ("Records reviewed", f"{counts['records']:,}"),
    ]
    r = 4
    for label, value in meta:
        ws.cell(row=r, column=1, value=label).font = Font(bold=True, color=INK)
        ws.cell(row=r, column=1).alignment = Alignment(vertical="top")
        ws.cell(row=r, column=3, value=value)
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="What is in this workbook").font = Font(
        bold=True, size=12, color=INK)
    r += 1
    for j, title in enumerate(("Sheet", "Rows", "What it contains"), start=1):
        cell = ws.cell(row=r, column=j, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = GRID
        cell.alignment = HDR_ALIGN
    r += 1
    sheets = [
        ("Matches", counts["match"],
         "Christie, English, buyable, publisher neither HarperCollins nor a known licensee"),
        ("Needs review", counts["review"],
         "Christie is credited but something is unclear; a person has to decide"),
        ("Could not read", counts["unreadable"],
         "The page was fetched but the details could not be parsed; check by hand if it matters"),
        ("Gone or changed", counts["gone"],
         "Was a match when first found, but is no longer one (removed, out of stock, re-published…)"),
    ]
    first_table_row = r
    for name, n, what in sheets:
        ws.cell(row=r, column=1, value=name).alignment = TOP
        c = ws.cell(row=r, column=2, value=n)
        c.alignment = Alignment(vertical="top", horizontal="right")
        ws.cell(row=r, column=3, value=what).alignment = TOP_WRAP
        for j in range(1, 4):
            ws.cell(row=r, column=j).border = GRID
        r += 1
    # total
    ws.cell(row=r, column=1, value="Total").font = Font(bold=True)
    total = ws.cell(row=r, column=2, value=sum(c[1] for c in sheets))
    total.font = Font(bold=True)
    total.alignment = Alignment(vertical="top", horizontal="right")
    for j in range(1, 4):
        ws.cell(row=r, column=j).border = GRID

    if publishers:
        r += 2
        ws.cell(row=r, column=1, value="Matches by publisher").font = Font(
            bold=True, size=12, color=INK)
        r += 1
        for j, title in enumerate(("Publisher", "Editions"), start=1):
            cell = ws.cell(row=r, column=j, value=title)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.border = GRID
        r += 1
        shown = publishers[:15]
        for name, n in shown:
            ws.cell(row=r, column=1, value=name).alignment = TOP
            c = ws.cell(row=r, column=2, value=n)
            c.alignment = Alignment(vertical="top", horizontal="right")
            ws.cell(row=r, column=1).border = GRID
            c.border = GRID
            r += 1
        if len(publishers) > len(shown):
            rest = len(publishers) - len(shown)
            more = sum(n for _, n in publishers[len(shown):])
            cell = ws.cell(row=r, column=1,
                           value=f"…and {rest} more publishers ({more} editions)")
            cell.font = Font(italic=True, color="7F7F7F")
            cell.alignment = TOP
            r += 1

    r += 1
    ws.cell(row=r, column=1, value="What the labels mean").font = Font(
        bold=True, size=12, color=INK)
    r += 1
    for j, title in enumerate(("Label", "Meaning"), start=1):
        cell = ws.cell(row=r, column=j, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = GRID
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
    r += 1
    for cls, meaning in WHY_REVIEW.items():
        ws.cell(row=r, column=1, value=WHY_REVIEW_SHORT[cls]).alignment = TOP
        cell = ws.cell(row=r, column=2, value=meaning)
        cell.alignment = TOP_WRAP
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
        for j in range(1, 4):
            ws.cell(row=r, column=j).border = GRID
        ws.row_dimensions[r].height = 15 * math.ceil(len(meaning) / 100)
        r += 1

    r += 2
    ws.cell(row=r, column=1, value="How to use this").font = Font(
        bold=True, size=12, color=INK)
    r += 1
    for line in [
        "Filter or sort with the arrow on any column heading.",
        "“Checked on” is the date the listing was last fetched. Prices and stock "
        "change, and a listing can disappear: a link that 404s is a listing that "
        "has gone, not a broken file.",
        "Use the “Reviewer notes” column (the last one) for anything you want to "
        "record. Your notes are kept when this workbook is regenerated; other "
        "edits are not, so change notes rather than other columns.",
        f"Evidence screenshots and saved pages are archived separately by the "
        "hunter and are not part of this file.",
    ]:
        cell = ws.cell(row=r, column=1, value="• " + line)
        cell.alignment = TOP_WRAP
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
        ws.row_dimensions[r].height = max(15, 15 * math.ceil(len(line) / 118))
        r += 1

    ws.freeze_panes = "A4"


def _sheet_state(ws, notes, printed):
    """Hidden bookkeeping: the notes to carry into the next export.

    Holds the ASINs in this workbook plus any ASIN that has ever carried a note,
    so a note is not lost when its listing drops out of the report and comes
    back later.
    """
    ws.sheet_state = "hidden"
    ws.append(["asin", "notes", "first_exported", "last_exported"])
    for asin, entry in sorted(notes.items()):
        if asin in printed or (entry.get("notes") or "").strip():
            ws.append([asin, entry.get("notes", ""),
                       entry.get("first_exported", ""),
                       entry.get("last_exported", "")])


# --- notes round-trip ---------------------------------------------------------

def read_notes(path):
    """{asin: {...}} from a previous export, or {} if there is none.

    The *visible* "Reviewer notes" column is the source of truth — that is what
    the recipient edits — and the hidden sheet is merged in for the ASINs whose
    listing has since dropped out of the report (so their notes are not lost).
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        wb = load_workbook(path, read_only=True)
    except Exception as e:
        logging.warning("could not read the previous workbook (%s); starting "
                        "fresh, so any reviewer notes in it will be lost", e)
        return {}
    try:
        notes = {}
        if STATE_SHEET in wb.sheetnames:
            for row in wb[STATE_SHEET].iter_rows(min_row=2, values_only=True):
                asin = str(row[0] or "").strip()
                if asin:
                    notes[asin] = {"notes": row[1] or "",
                                   "first_exported": row[2] or "",
                                   "last_exported": row[3] or ""}
        for name in (NOTES_SHEETS):
            if name not in wb.sheetnames:
                continue
            rows = wb[name].iter_rows(values_only=True)
            header = next(rows, None) or ()
            if NOTES_HEADER not in header or "ASIN" not in header:
                continue
            n_i, a_i = header.index(NOTES_HEADER), header.index("ASIN")
            for row in rows:
                if not row or a_i >= len(row):
                    continue
                asin = str(row[a_i] or "").strip()
                if not asin:
                    continue
                entry = notes.setdefault(asin, {"notes": "", "first_exported": "",
                                                "last_exported": ""})
                cell = row[n_i] if n_i < len(row) else None
                entry["notes"] = str(cell).strip() if cell else ""
        return notes
    finally:
        wb.close()


# --- the workbook -------------------------------------------------------------

def build_workbook(records, notes=None, generated=None):
    """Build the workbook in memory. Returns (workbook, counts).

    Pure: no disk access, so it can be tested without state.json.
    """
    generated = generated or datetime.now()
    notes = dict(notes or {})
    matches, review, unreadable, gone = route(records)
    counts = {"records": len(records), "match": len(matches),
              "review": len(review), "unreadable": len(unreadable),
              "gone": len(gone)}

    publishers = Counter((rec.get("publisher") or "(unknown)") for _, rec in matches)
    publishers = sorted(publishers.items(), key=lambda kv: (-kv[1], kv[0].lower()))

    wb = Workbook()
    wb.properties.title = "Agatha Christie on amazon.co.uk — review workbook"
    wb.properties.creator = "christie-hunter"
    wb.properties.description = (f"Generated {generated:%Y-%m-%d %H:%M} from "
                                 "state.json. Reviewer notes are preserved on "
                                 "re-export; other edits are not.")

    summary = wb.active
    summary.title = "Summary"
    _sheet_summary(summary, counts, publishers, generated)
    tabs = [("Matches", _sheet_matches, matches, "2E7D32"),
            ("Needs review", _sheet_review, review, "B26A00"),
            ("Could not read", _sheet_unreadable, unreadable, "808080"),
            ("Gone or changed", _sheet_gone, gone, "9C2A2A")]
    for name, render, rows, colour in tabs:
        ws = wb.create_sheet(name)
        render(ws, rows, notes)
        ws.sheet_properties.tabColor = colour
    printed = {asin for sheet in (matches, review, unreadable, gone)
               for asin, _ in sheet}
    _sheet_state(wb.create_sheet(STATE_SHEET), notes, printed)
    return wb, counts


def export(cfg, path=None, store=None):
    """Write (or refresh) the workbook. Offline, read-only, note-preserving."""
    store = store or Store(STATE_FILE)
    out = Path(cfg["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    path = Path(path) if path else out / WORKBOOK_NAME

    previous = read_notes(path)
    now = datetime.now().isoformat(timespec="seconds")
    carried = sum(1 for e in previous.values() if e.get("notes"))
    # keep every note we have ever seen, including for listings that have since
    # dropped out of the report entirely
    for asin in store.records:
        entry = previous.setdefault(asin, {"notes": "", "first_exported": ""})
        entry["last_exported"] = now
        if not entry.get("first_exported"):
            entry["first_exported"] = now
    for entry in previous.values():
        entry.setdefault("last_exported", now)

    wb, counts = build_workbook(store.records, previous)
    tmp = path.with_name(path.name + ".tmp")
    try:
        wb.save(tmp)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"could not write {tmp}: {e}") from None
    try:
        os.replace(tmp, path)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"could not replace {path} ({e}). If it is open in Excel, close it "
            "and run this again — your previous notes are untouched.") from None

    print(f"wrote {path}")
    print(f"  {counts['match']:4}  matches")
    print(f"  {counts['review']:4}  need review")
    print(f"  {counts['unreadable']:4}  could not be read")
    print(f"  {counts['gone']:4}  gone or changed since they were found")
    if carried:
        print(f"  {carried:4}  reviewer notes carried over from the previous workbook")
    return counts
