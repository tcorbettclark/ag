"""Constant values: Amazon endpoints, state/config paths, and the text patterns
that decide what a page is."""

import re
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://www.amazon.co.uk"
AUTHOR_PAGE = "https://www.amazon.co.uk/Agatha-Christie/e/B000APENBC"
STATE_FILE = Path("state.json")
CONFIG_FILE = Path("config.json")

# --- HarperCollins imprint matching -----------------------------------------

# Definite HarperCollins imprints -> excluded_harpercollins
HC_DEFINITE = [
    r"harper\s*-?\s*collins",                                  # HarperCollins, Harper Collins
    r"william\s+collins",
    r"collins\s+crime\s+club",
    r"collins\s+gems?\b",
    r"\bcollins\b",                                            # bare Collins = historic HC imprint
    r"\bfontana\b",
    # NB: no `fiction` here — "HarperFiction" is a licensed audio publisher (config.json)
    r"harper\s+(?:press|voyager|element|audio|design|luxe|one|true\s*crime|children)",
    r"harpercollins",                                          # no-space variants
    r"william\s+morrow",
    r"4\s*-?\s*th\s+estate|fourth\s+estate",
    r"borough\s+press",
]
# Ambiguous tokens that historically also exist outside HC -> unclear (human review)
HC_AMBIGUOUS = [
    r"\bharper\b",
    r"\bavon\b",
]

# Author-line contamination: Christie credited, but not as the author
AUTHOR_CONTAMINATION = [
    "foreword", "afterword", "introduction", "preface", "inspired", "reimagin",
    "featuring", "based on", "creator of", "creator", "edited by", "curated by",
]

# --- what a block looks like -------------------------------------------------
#
# These are the *captcha interstitial* markers and nothing else. The generic
# bot boilerplate ("api-services-support@amazon.com", "to discuss automated
# access") is on Amazon's ordinary 404 page too, and matching on it turned a
# single dead ASIN into a 65-minute cooldown loop plus an aborted 14h run.
# Dead pages are now handled by HTTP status; anything else small and odd is an
# UnexpectedPage, which is retried rather than escalated.
BLOCK_MARKERS = [
    "enter the characters you see below",
    "to continue, please type the characters",
    "robot check",
]

# A captcha interstitial is a few kB; a real page is hundreds. This keeps the
# marker search from ever firing on a full (large) page that merely quotes
# bot-block text somewhere in its markup.
BLOCK_MAX_BYTES = 60_000

# Positive markers: proof that we got the kind of page we asked for. Absence of
# these on a 200 means a soft block or a layout change — never a silent success.
EXPECT_MARKERS = {
    "product": ['id="producttitle"', "dp-container", "productdetails"],
    "search": ['data-component-type="s-search-result"', 'id="search"',
               "no results for"],
}

# Pages that are real Amazon content but never a book listing — the Prime Video
# page for a film adaptation turns up constantly in a Christie book search.
# Only consulted when the product-page markers are already missing, so a real
# book page can never be diverted here.
NON_BOOK_MARKERS = ["/gp/video", "instant-video", "prime video"]

# Search-result cards that are not books at all (Amazon mixes these into
# organic results). Cards are also dropped if they do not mention Christie;
# see Crawler._card_wanted.
CARD_NOISE = ["prime video", "watch now", "prime video channels"]

# Fallback only: the real UA is read from the running browser at startup so
# that it cannot disagree with the browser's own userAgentData / TLS stack.
UA_TEMPLATE = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} "
               "Safari/537.36")

VIEWPORTS = [(1920, 1080), (1440, 900), (1536, 864), (1366, 768), (1280, 800)]

ASIN_RE = re.compile(r'(?:data-asin="|/(?:dp|gp/product)/)([A-Z0-9]{10})')

# session cookies persist across runs and across session rotations
SESSION_FILE = ".session.json"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
