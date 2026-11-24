"""Every regex and lexicon used by the text engines (PRD §0.2.1, Appendix B, §7.2–7.4).

Engine modules must import patterns from here; never inline regexes.
"""

from __future__ import annotations

import re

I = re.IGNORECASE  # noqa: E741
IM = re.IGNORECASE | re.MULTILINE

# ---------------------------------------------------------------------------
# §7.2.1 HTML preprocessing
# ---------------------------------------------------------------------------
HIDDEN_STYLE = re.compile(r"display:\s*none", I)
PAGE_NUMBER_LINE = re.compile(r"^\d{1,3}$")
TOC_LINE = re.compile(r"^table of contents$", I)
NUMERIC_CELL = re.compile(r"^[\$\(\)\d,.\-%\s]+$")
WHITESPACE = re.compile(r"\s+")

# ---------------------------------------------------------------------------
# §7.2.2 Section location
# ---------------------------------------------------------------------------
# narrative: headings laid out as table rows serialize as "ITEM 7. | MANAGEMENT'S ..."
# (§7.2.1 " | " cell join; seen in WMT 10-Ks), so an optional "|" is allowed after the
# item number. Otherwise identical to the PRD patterns (docs/decisions/narrative.md D-302).
MDA_10K_START = re.compile(r"^item\s*7\s*[.:\-]?\s*(?:\|\s*)?management'?s?\s+discussion", IM)
MDA_10K_END = re.compile(r"^item\s*(7a|8)\b", IM)
MDA_10Q_START = re.compile(r"^item\s*2\s*[.:\-]?\s*(?:\|\s*)?management'?s?\s+discussion", IM)
MDA_10Q_END = re.compile(r"^(item\s*3\b|item\s*4\s*[.:\-]?\s*(?:\|\s*)?controls|part\s+ii\b)", IM)
INCORPORATED_BY_REFERENCE = re.compile(r"incorporated\s+(herein\s+)?by\s+reference", I)
EX13_MDA_START = re.compile(r"^.{0,20}management'?s\s+discussion\s+and\s+analysis", IM)
EX13_MDA_END = re.compile(
    r"^.{0,20}(report of independent registered|consolidated (balance sheets?|statements? of))", IM
)
FORWARD_LOOKING_PARA = re.compile(r"forward-looking statements", I)
BOILERPLATE_PARA = re.compile(r"forward-looking statements|private securities litigation reform", I)
# footnotes/proxy: optional "| " after the item number (table-row headings).
PART_III_ITEM11_START = re.compile(r"^item\s*11\s*[.:\-]?\s*(?:\|\s*)?executive\s+compensation", IM)
PART_III_ITEM11_END = re.compile(r"^item\s*12\b", IM)

# ---------------------------------------------------------------------------
# §7.2.3 Sentence splitting
# ---------------------------------------------------------------------------
ABBREVIATIONS = (
    "Inc.",
    "Corp.",
    "Co.",
    "Ltd.",
    "U.S.",
    "No.",
    "vs.",
    "e.g.",
    "i.e.",
    "Mr.",
    "Ms.",
    "Dr.",
    "approx.",
    "Jan.",
    "Feb.",
    "Mar.",
    "Apr.",
    "Jun.",
    "Jul.",
    "Aug.",
    "Sep.",
    "Sept.",
    "Oct.",
    "Nov.",
    "Dec.",
)
DECIMAL_POINT = re.compile(r"(?<=\d)\.(?=\d)")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"(]?[A-Z])")
TOKEN = re.compile(r"[A-Za-z0-9']+")
# narrative: abbreviation masking for §7.2.3 (longest first; not preceded by a letter or
# period, so "Inc." in "Inc." matches but "inc." inside "zinc." does not). Case-sensitive.
ABBREVIATION_RE = re.compile(
    r"(?<![A-Za-z.])(?:"
    + "|".join(re.escape(a) for a in sorted(ABBREVIATIONS, key=len, reverse=True))
    + r")"
)

# ---------------------------------------------------------------------------
# §7.2.4 / Appendix B claim clusters
# ---------------------------------------------------------------------------
DEMAND = re.compile(
    r"\b(strong|robust|healthy|solid|record|accelerat\w+)[\s-]+(\w+[\s-]+){0,3}"
    r"(demand|growth|orders|bookings|backlog|pipeline)\b|market[\s-]+share[\s-]+gains?",
    I,
)
MARGIN = re.compile(
    r"pricing[\s-]+power|margin[\s-]+expansion|disciplined[\s-]+cost|cost[\s-]+discipline|"
    r"operating[\s-]+leverage|operational[\s-]+efficienc\w+",
    I,
)
ONETIME = re.compile(
    r"transitory|one[\s-]time|non[\s-]?recurring|isolated|unusual[\s-]+(item|charge)s?", I
)
CLUSTERS: dict[str, re.Pattern[str]] = {"DEMAND": DEMAND, "MARGIN": MARGIN, "ONETIME": ONETIME}

# Applied per token (the 6 tokens before the match). "n't" handled by suffix check.
NEGATION_TOKEN = re.compile(
    r"^(no|not|never|without|lack\w*|absence|weak\w*|soft\w*|declin\w+|lower|decreas\w+)$", I
)
NEGATION_SUFFIX = "n't"
FORWARD = re.compile(
    r"\b(expect\w*|anticipat\w*|believe\w*|intend\w*|plan\w*|forecast\w*|outlook|guidance|"
    r"may|could|should|would|will|if)\b",
    I,
)
HEDGE = re.compile(
    r"\b(may|might|could|possibly|potentially|uncertain\w*|believes?)\b|subject[\s-]+to|"
    r"contingent[\s-]+(on|upon)|no[\s-]+assurance|cannot[\s-]+(predict|assure|estimate)|"
    r"reasonably[\s-]+possible|substantial[\s-]+doubt",
    I,
)

# ---------------------------------------------------------------------------
# §7.3 Footnotes
# ---------------------------------------------------------------------------
NOTES_HEADING = re.compile(
    r"notes\s+to\s+(the\s+)?(condensed\s+)?(consolidated\s+)?financial\s+statements", I
)
# footnotes/proxy: item/note headings laid out as table rows serialize as "ITEM 8. | FINANCIAL ..."
# (optional "| " after the number), same fix as D-302.
NOTE_HEADER = re.compile(r"^(note\s+)?\d{1,2}\s*[.:\-]?\s*(?:\|\s*)?[A-Z]", re.IGNORECASE)
ITEM8_START = re.compile(r"^item\s*8\s*[.:\-]?\s*(?:\|\s*)?financial\s+statements", IM)
ITEM8_END = re.compile(r"^item\s*9\b", IM)
TEN_Q_PART1_ITEM1_START = re.compile(r"^item\s*1\s*[.:\-]?\s*(?:\|\s*)?financial\s+statements", IM)
TEN_Q_PART1_ITEM1_END = re.compile(
    r"^item\s*2\s*[.:\-]?\s*(?:\|\s*)?management'?s?\s+discussion", IM
)

NOTE_CATEGORIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("revenue", re.compile(r"revenue", I)),
    ("debt", re.compile(r"debt|borrowing|credit", I)),
    ("inventory", re.compile(r"inventor", I)),
    ("contingencies", re.compile(r"commitment|contingenc|legal|litigation", I)),
    ("goodwill", re.compile(r"goodwill|intangible", I)),
    ("related_party", re.compile(r"related[\s_-]?part", I)),
)

# §7.3.3 normalization, applied in this order
NORM_DATE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b|"
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
    I,
)
NORM_AMOUNT = re.compile(r"\(?\$\s?[\d,]+(\.\d+)?\)?(\s?(thousand|million|billion|[MB])\b)?", I)
NORM_PERCENT = re.compile(r"[\d,.]+\s?(%|percent|basis points|bps)", I)
NORM_YEAR = re.compile(r"\b(19|20)\d{2}\b")
NORM_NUMBER = re.compile(r"\b[\d,]+(\.\d+)?\b")
NORMALIZATION_ORDER: tuple[tuple[re.Pattern[str], str], ...] = (
    (NORM_DATE, "<DATE>"),
    (NORM_AMOUNT, "<AMT>"),
    (NORM_PERCENT, "<PCT>"),
    (NORM_YEAR, "<YEAR>"),
    (NORM_NUMBER, "<NUM>"),
)

# §7.3.5 material edit
MATERIAL_TERMS = re.compile(
    r"recogni[sz]|covenant|default|waiver|reserve|allowance|impair|probable|"
    r"reasonably possible|estimat|useful li(fe|ves)|going concern|restat|material weakness|"
    r"related part|guarantee",
    I,
)
MATERIAL_EXCLUDE = re.compile(r"ASU|accounting standards update|recently (issued|adopted)", I)

# Modal strengths; longer phrases first.
MODALS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\bmay not be able to\b", I), 1),
    (re.compile(r"\bintends? to seek\b", I), 1),
    (re.compile(r"\bis required to\b", I), 3),
    (re.compile(r"\bare required to\b", I), 3),
    (re.compile(r"\bno assurance\b", I), 1),
    (re.compile(r"\bexpects? to\b", I), 2),
    (re.compile(r"\bintends? to\b", I), 2),
    (re.compile(r"\bplans? to\b", I), 2),
    (re.compile(r"\bseeks? to\b", I), 1),
    (re.compile(r"\bwill\b", I), 3),
    (re.compile(r"\bshall\b", I), 3),
    (re.compile(r"\bmust\b", I), 3),
    (re.compile(r"\bmay\b", I), 1),
    (re.compile(r"\bmight\b", I), 1),
    (re.compile(r"\bcould\b", I), 1),
)

# ---------------------------------------------------------------------------
# §7.4 Proxy
# ---------------------------------------------------------------------------
CDA_START = re.compile(r"compensation\s+discussion\s+(and|&)\s+analysis", I)
CDA_END = re.compile(r"compensation\s+committee\s+report|summary\s+compensation\s+table", I)
CDA_FALLBACK_START = re.compile(r"executive\s+compensation", I)
INCENTIVE_CONTEXT = re.compile(
    r"weight\w*|target|threshold|maximum|payout|performance\s+(metric|measure|goal)|"
    r"annual\s+incentive|bonus|PSU|performance\s+share",
    I,
)
PROXY_EXCLUDE_HEADING = re.compile(r"non-gaap|reconciliation|definitions?|appendix|annex", I)
PROXY_EXCLUDE_SENTENCE = re.compile(r"is defined as|we define|\bmeans\b", I)
WEIGHT_PERCENT = re.compile(r"(\d{1,3})\s?%")
WEIGHT_HEADER = re.compile(r"weight", I)

PROXY_ALIASES: dict[str, re.Pattern[str]] = {
    "ADJ_EBITDA": re.compile(r"adjusted[\s-]+ebitda", I),
    "NONGAAP_EPS": re.compile(
        r"(adjusted|non[\s-]?gaap)[\s-]+(diluted[\s-]+)?(eps|earnings[\s-]+per[\s-]+share)", I
    ),
    "REVENUE": re.compile(r"(net[\s-]+)?(revenue|sales)([\s-]+growth)?", I),
    "TSR": re.compile(r"total[\s-]+shareholder[\s-]+return|\bTSR\b", I),
    "FCF": re.compile(r"free[\s-]+cash[\s-]+flow", I),
    "ROIC": re.compile(r"return[\s-]+on[\s-]+invested[\s-]+capital|\bROIC\b|\bROCE\b", I),
    "M_AND_A": re.compile(
        r"acquisitions?[\s-]+completed|acquired[\s-]+revenue|deal[\s-]+volume", I
    ),
}

# ---------------------------------------------------------------------------
# §0.1.8 banned words in user-facing strings about an issuer
# ---------------------------------------------------------------------------
BANNED_WORDS = re.compile(
    r"\b(fraud\w*|manipulat\w*|misleading|deceptive|stuffing|cooking the books)\b", I
)

# 8-K exhibit fallback (§6.4)
EX99_1_NAME = re.compile(r"ex-?99[._-]?1", I)

# ---------------------------------------------------------------------------
# footnotes/proxy: structural patterns for engine/footnote_diff.py and proxy_xray.py
# ---------------------------------------------------------------------------
IX_TEXTBLOCK_HINT = re.compile(r"<ix:nonnumeric", I)
IX_TEXTBLOCK_OPEN = re.compile(r"<ix:nonnumeric\b[^>]*\bname=\"[^\"]*textblock\"", I)
XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>", I)
NOTE_TITLE_PREFIX = re.compile(r"^\s*(note\s+)?\d{1,2}\s*[.:\-]?\s*(?:\|\s*)?", I)
NOTE_TITLE_PREFIX_BARE = re.compile(r"^\s*note\s+\d{1,2}\s*[.:\-]?\s*", I)
NOTE_NUMBER = re.compile(r"^\s*(?:note\s+)?(\d{1,2})", I)
TRAILING_PAGE_NUMBER = re.compile(r"[\s.…_]*\d{1,3}\s*$")
CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
ALPHA_TOKEN = re.compile(r"[a-z]+")
NON_SPACE = re.compile(r"\S+")
XBRL_PREFIX = re.compile(r"^[^:]*:")
IMG_TAG = re.compile(r"<img\b", I)
# A CD&A start counts when it sits on a heading-like line or in a self-reference such as
# "This Compensation Discussion and Analysis ..." (text before the match on its line).
CDA_SELF_REFERENCE_PREFIX = re.compile(r"^\s*(this|the|our)\s+$", I)
