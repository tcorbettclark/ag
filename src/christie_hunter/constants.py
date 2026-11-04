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

BLOCK_MARKERS = [
    "robot check", "enter the characters you see below",
    "api-services-support@amazon.com", "to discuss automated access",
    "sorry, something went wrong", "access denied", "captcha",
    "to continue, please type the characters",
]

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]
VIEWPORTS = [(1920, 1080), (1440, 900), (1536, 864), (1366, 768), (1280, 800)]

ASIN_RE = re.compile(r'(?:data-asin="|/(?:dp|gp/product)/)([A-Z0-9]{10})')


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
