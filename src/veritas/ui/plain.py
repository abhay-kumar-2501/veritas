"""Plain-English layer: turns engine output into sentences a non-specialist can read.

Rules (docs/decisions/ui.md D-610):

* Every number in a plain sentence comes from the same check/finding the technical
  view shows; nothing here computes a new metric or changes a severity.
* Wording stays at the level of a screen: "worth a closer look", never a judgement
  about the company (no banned words, no "wrong", no "suspicious").
* If an input is missing, the sentence falls back to a generic description rather
  than guessing a value.

Pure functions; no Streamlit dependency.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from veritas.config import t
from veritas.engine.narrative import _has_no_data
from veritas.ui.components import as_list, fmt_money, get

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

SEVERITY_PLAIN: dict[str, str] = {
    "CRITICAL": "Big gap",
    "WARNING": "Worth a closer look",
    "NO_FLAG": "In line",
}

# Plain names for the scored deduction categories (score panel).
DEDUCTION_PLAIN: dict[str, str] = {
    "accounting": "Warning signs in the numbers",
    "footnotes": "Edits to the fine print",
    "hedging": "More cautious wording",
    "incentives": "How executives are paid",
}

# Plain names for the modules (landing + chips).
MODULE_PLAIN: dict[str, tuple[str, str]] = {
    "A": ("Do their words match their numbers?", "Narrative vs Numbers"),
    "B": ("What changed in the fine print?", "Footnote Redline"),
    "C": ("What are executives paid to hit?", "Proxy X-Ray"),
    "D": ("One score for all of it", "Consistency Score"),
}

CLUSTER_TOPIC: dict[str, str] = {
    "DEMAND": "strong demand or sales growth",
    "MARGIN": "keeping more profit from each dollar of sales",
    "ONETIME": "some costs being one-offs",
}


@dataclass(frozen=True)
class CheckPlain:
    name: str  # what a newcomer would call it
    question: str  # the question the check answers
    why: str  # why a gap matters
    analogy: str  # everyday comparison


CHECK_PLAIN: dict[str, CheckPlain] = {
    "A1_DSO": CheckPlain(
        "How long customers take to pay",
        "How many days' worth of sales do customers still owe the company?",
        "When customers take longer and longer to pay, sales on paper can run ahead of the "
        "money actually coming in.",
        "Like a shop where more customers each month say “put it on my tab”.",
    ),
    "A2_DIO": CheckPlain(
        "Unsold goods on hand",
        "How many days of sales are sitting unsold in the warehouse?",
        "Goods piling up faster than they sell can mean demand is slowing, or that some of "
        "them may later have to be sold off cheaply.",
        "Like a bakery baking more loaves each week than it sells.",
    ),
    "A3_ACCRUALS": CheckPlain(
        "Profit vs. cash coming in",
        "Does the profit the company reports turn into real cash?",
        "Profit on paper can include money the company hasn't received yet; cash is what "
        "actually arrived. A gap means part of the profit hasn't shown up as cash yet.",
        "Like a payslip that shows more than what actually lands in your bank account.",
    ),
    "A4_CAPRATE": CheckPlain(
        "Software costs spread over future years",
        "Is a growing share of the company's software and research spending being counted as "
        "a long-term investment?",
        "Spending counted as an investment is spread over several years instead of being "
        "subtracted from this year's profit, so doing more of it makes this year's profit "
        "look higher. It's allowed; the check watches whether it's rising.",
        "Like spreading the cost of a new laptop over three years instead of counting it all "
        "today.",
    ),
    "A5_RESTRUCT": CheckPlain(
        "Repeated “one-time” costs",
        "Do costs described as one-off (like layoffs or closing sites) show up again and again?",
        "Costs that come back every year are part of the normal cost of running the "
        "business, even when they're called one-time.",
        "Like a “one-off” car repair you pay for every few months.",
    ),
    "X_CAPEX_DA": CheckPlain(
        "New equipment vs. wear and tear",
        "Is the company spending more on new equipment and buildings than it wears out?",
        "Shown for context only.",
        "Like comparing what you spend fixing up a house with how fast it ages.",
    ),
    "X_DPO": CheckPlain(
        "How long it takes to pay its suppliers",
        "How long does the company take to pay its own bills?",
        "Paying suppliers later keeps more cash in hand for a while. Shown for context only.",
        "Like paying your credit card later to keep cash in hand.",
    ),
    "X_FCF": CheckPlain(
        "Spare cash",
        "How much cash is left after running the business and paying for new equipment?",
        "Shown for context only.",
        "Like your take-home pay minus what it costs to keep your home in shape.",
    ),
    "X_FCF_SBC": CheckPlain(
        "Spare cash after paying staff in shares",
        "How much spare cash is left if paying staff in company shares is counted as a cost?",
        "Shown for context only.",
        "Like counting the gift cards you hand out as part of your spending.",
    ),
    "X_ROIC": CheckPlain(
        "Profit on money invested",
        "How much profit does each dollar put into the business earn?",
        "Shown for context only.",
        "Like the yearly return on money you put into a small business.",
    ),
    "X_RESTATED": CheckPlain(
        "Past figures changed later",
        "Did the company later change numbers it had already reported?",
        "Shown for context only.",
        "Like a receipt reissued with a different total.",
    ),
}

# Jargon a newcomer meets on the page, with a one-line plain definition (hover tips).
GLOSSARY: dict[str, str] = {
    "SEC": "The U.S. Securities and Exchange Commission: the government agency that public "
    "companies must file their reports with.",
    "EDGAR": "The SEC's free public database of company filings.",
    "10-K": "A company's annual report to the SEC, with audited financial statements.",
    "10-Q": "A company's quarterly report to the SEC (unaudited).",
    "8-K": "A report filed when something significant happens, such as quarterly earnings.",
    "DEF 14A": "The proxy statement: sent to shareholders before the annual meeting; it "
    "explains how top executives are paid.",
    "MD&A": "Management's Discussion and Analysis: the part of a report where managers "
    "explain the results in their own words.",
    "XBRL": "Machine-readable tags attached to each number in a filing, so the figures can "
    "be read exactly as reported.",
    "CIK": "Central Index Key: the SEC's ID number for a company.",
    "SIC": "Standard Industrial Classification: a code for the company's industry.",
    "FY end": "The month and day the company's financial year ends (MMDD).",
    "Footnotes": "The detailed notes printed under the financial statements; they explain "
    "how the numbers were produced and disclose risks.",
    "TTM": "Trailing twelve months: the most recent four quarters added together.",
    "Coverage": "How many of the scored checks we could actually run for this company. "
    "Checks we could not run are listed, never counted as a pass.",
    "Non-GAAP": "An adjusted figure the company defines itself, outside standard accounting "
    "rules (for example “adjusted EBITDA” or “core EPS”).",
    "SBC": "Stock-based compensation: paying employees in company shares instead of cash.",
    "Goodwill": "The extra amount paid when buying another company, above the value of its "
    "net identifiable assets.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUFFIX = re.compile(
    r"[,\s]+(inc\.?|corp\.?|corporation|co\.?|company|ltd\.?|plc|holdings?|group)$", re.I
)
SHORT_NAMES: dict[str, str] = {
    "NVDA": "NVIDIA",
    "WMT": "Walmart",
    "TSLA": "Tesla",
    "BA": "Boeing",
}


def short_name(result: Any) -> str:
    """A readable company name: 'BOEING CO' -> 'Boeing'."""
    ticker = str(get(result, "ticker", "") or "").upper()
    if ticker in SHORT_NAMES:
        return SHORT_NAMES[ticker]
    name = str(get(result, "company_name", "") or ticker or "This company").strip()
    for _ in range(2):
        name = _SUFFIX.sub("", name).strip()
    if name.isupper() and len(name) > 4:
        name = name.title()
    return name or "This company"


def month_year(value: Any) -> str:
    if isinstance(value, str) and len(value) >= 10:
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return str(value)
    if isinstance(value, (date, datetime)):
        return value.strftime("%B %Y")
    return "the latest period"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return None if v != v else v  # NaN -> None


def _days(v: float) -> str:
    return f"{v:,.0f} day" + ("" if round(v) == 1 else "s")


# ---------------------------------------------------------------------------
# Check-level sentences
# ---------------------------------------------------------------------------


def numbers_sentence(check: Any, company: str) -> str | None:
    """One sentence stating what the numbers show, using only the check's values."""
    cid = str(get(check, "check_id", ""))
    outputs = get(check, "outputs", {}) or {}
    inputs = get(check, "inputs", {}) or {}
    if not isinstance(outputs, Mapping) or not isinstance(inputs, Mapping):
        return None
    when = month_year(get(check, "period_end"))

    if cid == "A1_DSO":
        cur, prior = _num(outputs.get("dso")), _num(outputs.get("dso_prior_year"))
        if cur is None or prior is None:
            return None
        trend = _trend(cur, prior, unit="days", delta=_num(outputs.get("delta_dso_days")))
        return (
            f"At the end of {when}, {company}'s customers still owed it about "
            f"{_days(cur)}' worth of sales, {trend} a year earlier ({_days(prior)})."
        )
    if cid == "A2_DIO":
        cur, prior = _num(outputs.get("dio")), _num(outputs.get("dio_prior_year"))
        if cur is None or prior is None:
            return None
        trend = _trend(cur, prior, unit="days", delta=_num(outputs.get("delta_dio_days")))
        return (
            f"At the end of {when}, {company} had enough unsold goods on hand to cover about "
            f"{_days(cur)} of sales, {trend} a year earlier ({_days(prior)})."
        )
    if cid == "A3_ACCRUALS":
        return _a3_sentence(check, outputs, inputs, company, when)
    if cid == "A4_CAPRATE":
        cur = _num(outputs.get("cap_rate"))
        prior = _num(outputs.get("cap_rate_prior_year"))
        if cur is None or prior is None:
            return None
        pp = _num(outputs.get("cap_rate_change_pp"))
        trend = _trend(
            cur * 100, prior * 100, unit="percentage points", delta=None if pp is None else pp * 100
        )
        return (
            f"Over the 12 months to {when}, {company} counted about {cur * 100:,.0f}% of its "
            f"software and research spending as a long-term investment instead of a cost, "
            f"{trend} a year earlier ({prior * 100:,.0f}%)."
        )
    if cid == "A5_RESTRUCT":
        return _a5_sentence(check, outputs, company, when)
    return None


def _a3_sentence(
    check: Any, outputs: Mapping[str, Any], inputs: Mapping[str, Any], company: str, when: str
) -> str | None:
    ni, ocf = _num(inputs.get("NI_ttm")), _num(inputs.get("OCF_ttm"))
    if ni is None or ocf is None:
        return None
    earned = (
        f"reported {fmt_money(ni)} of profit" if ni > 0 else f"reported a loss of {fmt_money(-ni)}"
    )
    if ocf >= 0:
        cash = f"brought in {fmt_money(ocf)} of cash from its day-to-day business"
    else:
        cash = f"its day-to-day business used up {fmt_money(-ocf)} of cash"
    joiner = " and " if ocf >= 0 else (" while " if ni > 0 else " and ")
    out = f"Over the 12 months to {when}, {company} {earned}{joiner}{cash}"
    ccr = _num(outputs.get("ccr"))  # the engine's own ratio; None when not meaningful
    if ccr is not None and 0 <= ccr < 1 and round(ccr * 100) < 100:
        out += f": about {ccr * 100:,.0f}\u00a2 of cash for every $1 of profit."
    elif ccr is not None and 0 <= ccr < 1:
        out += ": about as much cash as profit."
    elif ccr is not None and ccr >= 1:
        out += ": more cash than profit."
    else:
        out += "."
    ar = _num(outputs.get("accrual_ratio"))
    if ar is not None and ar > 0 and round(ar * 100) >= 1:
        out += (
            f" The gap is about {ar * 100:,.0f}% of the value of everything the company owns "
            "(averaged over the year)."
        )
    notes = " ".join(str(a) for a in as_list(get(check, "annotations"))).lower()
    growth = _num(outputs.get("OCF_ttm_growth"))
    if "operating cash flow fell" in notes and growth is not None:
        out += (
            " Profit rose from a year earlier while cash from the day-to-day business fell by "
            f"about {abs(growth) * 100:,.0f}%."
        )
    return out


def _a5_sentence(check: Any, outputs: Mapping[str, Any], company: str, when: str) -> str | None:
    n4 = _num(outputs.get("material_quarters_last4"))
    if n4 is None:
        return None
    pct = f"{t('RESTRUCT_MATERIAL_PCT') * 100:g}%"
    q = "quarter" if round(n4) == 1 else "quarters"
    out = (
        f"In the last four quarters to {when}, {company} reported reorganization costs (such "
        f"as layoffs or closing sites) of at least {pct} of that quarter's sales in "
        f"{n4:,.0f} {q}."
    )
    if str(get(check, "severity")) == "CRITICAL":
        n8 = _num(outputs.get("material_quarters_last8"))
        years = _num(outputs.get("material_fiscal_years_last3"))
        if n8 is not None:
            out += f" Over the last eight quarters it did so in {n8:,.0f}."
        if years is not None and round(years) >= 3:
            out += " It also did so in each of the last three financial years."
    unk = _num(outputs.get("quarters_unknown_last4"))
    if unk:
        out += (
            f" We couldn't work out the figure for {unk:,.0f} of those four quarters, so the "
            "count may be low."
        )
    return out


def _trend(cur: float, prior: float, unit: str, delta: float | None = None) -> str:
    """Change between the two values as displayed (rounded), so the arithmetic on screen
    adds up; the unit is singular for 1. When the engine's own change (`delta`) rounds to
    a different whole number, it is shown with one decimal instead, so the stated change
    never contradicts the threshold that raised the flag."""
    diff = round(cur) - round(prior)
    if delta is not None and round(delta) != diff:
        if round(abs(delta), 1) == 0:
            return "about the same as"
        return f"{'up' if delta > 0 else 'down'} {abs(delta):,.1f} {unit} from"
    if diff == 0:
        return "about the same as"
    word = unit[:-1] if abs(diff) == 1 and unit.endswith("s") else unit
    return f"{'up' if diff > 0 else 'down'} {abs(diff):,} {word} from"


_ANNOT_PLAIN: dict[str, str] = {
    "acquisition-distorted": "The company spent heavily on buying other businesses over the "
    "past year, or the goodwill it records from past purchases grew by 10% or more. That can "
    "throw this measure off; read it with extra caution.",
    "low reliability for financials": "For banks, insurers and similar companies this measure "
    "is less reliable, so it isn't scored.",
    "payables stretch": "It is taking noticeably longer to pay its suppliers than a year "
    "earlier, which temporarily boosts cash.",
    "capex at least 2x": "It is spending at least twice as much on new equipment and "
    "buildings as they wear out, and that gap is growing.",
    "restated": "The company later revised some of these figures; the revised values are used.",
}


def annotation_plain(annotation: Any) -> str | None:
    text = str(annotation or "").lower()
    for key, plain in _ANNOT_PLAIN.items():
        if key in text:
            return plain
    return None


# ---------------------------------------------------------------------------
# Finding-level sentences
# ---------------------------------------------------------------------------


def finding_plain(finding: Any, company: str) -> str | None:
    """The 'In plain English' line for a Module A finding card."""
    check = get(finding, "check")
    if check is None:
        return None
    numbers = numbers_sentence(check, company)
    if numbers is None:
        return None
    sev = str(get(finding, "severity", ""))
    claims = as_list(get(finding, "claims"))
    topic = next(
        (
            CLUSTER_TOPIC[str(get(cl, "cluster"))]
            for cl in claims
            if get(cl, "cluster") in CLUSTER_TOPIC
        ),
        None,
    )
    prefix = (
        "Earlier period, not counted in the score. "
        if str(get(finding, "category", "")) == "accounting_prior_period"
        else ""
    )
    if get(finding, "contradiction", False) and topic:
        return (
            f"{prefix}Managers talked about {topic}, but this measure raised a flag for the same "
            f"period: "
            f"{_lower_first(numbers)}"
        )
    if sev in ("CRITICAL", "WARNING") and not claims:
        cid = str(get(check, "check_id", ""))
        if cid == "A3_ACCRUALS":  # no statement type is ever paired with this check
            return prefix + numbers
        return (
            f"{prefix}{numbers} We didn't find a statement from managers about this to compare it "
            "with (we only look for certain kinds of statements, such as claims about strong "
            "demand or rising profits)."
        )
    if sev == "NO_FLAG" and topic:
        return (
            f"Managers talked about {topic}, and this measure raised no flag: "
            f"{_lower_first(numbers)}"
        )
    return numbers


def _lower_first(text: str) -> str:
    """Numbers sentences start with a preposition ("In", "At", "Over")."""
    return text[:1].lower() + text[1:]


_SBC_FCF = re.compile(r"SBC (\S+) vs free cash flow (\S+) \(TTM\)")
_SBC_REV = re.compile(r"SBC (\S+) is (\S+) of revenue (\S+) \(TTM\)")
_GW = re.compile(r"goodwill is (\S+) of assets")
_TERM = re.compile(r"cites '([^']+)'")


def _money_value(text: str) -> float | None:
    m = re.fullmatch(r"(\u2212|-)?\$([\d,.]+)([KMB]?)", text.strip().rstrip(";,"))
    if not m:
        return None
    v = float(m.group(2).replace(",", "")) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(3)]
    return -v if m.group(1) else v


def incentive_plain(finding: Any) -> str | None:
    fid = str(get(finding, "id", ""))
    expl = str(get(finding, "explanation", "") or "")
    if fid == "C1":
        sbc_txt: str | None = None
        clauses: list[str] = []
        m = _SBC_FCF.search(expl)
        if m:
            sbc, fcf = _money_value(m.group(1)), _money_value(m.group(2))
            if sbc is not None and fcf is not None and fcf > 0:
                sbc_txt = m.group(1)
                # The engine fires when stock pay is at least half of free cash flow
                # (or more than all of it); no new percentage is computed here.
                cmp = "more than" if sbc > fcf else "at least half of"
                clauses.append(
                    f"{cmp} the {m.group(2)} of spare cash (\u201cfree cash flow\u201d) the "
                    "business produced after its running costs and new equipment"
                )
        m = _SBC_REV.search(expl)
        if m:
            sbc_txt = sbc_txt or m.group(1)
            clauses.append(f"equal to {m.group(2)} of its sales")
        tail = (
            f" Over the last 12 months the company recorded {sbc_txt} of pay given to all "
            "employees in company shares, " + " and ".join(clauses) + "."
            if sbc_txt and clauses
            else ""
        )
        return (
            "Part of the top bosses' pay appears to depend on a custom \u201cadjusted\u201d "
            "figure that the company calculates itself, instead of the standard accounting "
            "version." + tail + " Some custom figures leave out pay given in shares; we did not "
            "check whether this company's figure does."
        )
    if fid == "C2":
        m = _GW.search(expl)
        bits = []
        if "rose" in expl:
            bits.append("that share rose over the past year")
        if "acquisition guard" in expl:
            bits.append(
                "it recently spent heavily on buying other businesses, or its goodwill grew "
                "by 10% or more"
            )
        share = (
            f" Goodwill makes up {m.group(1)} of everything the company owns"
            if m
            else " Goodwill is a large part of everything the company owns"
        )
        return (
            "Top bosses' pay appears to be partly tied to sales or to deals, and much of what "
            "the company owns on paper is \u201cgoodwill\u201d: the extra it paid, above the "
            "value of what it got, when buying other businesses."
            + share
            + (", and " + " and ".join(bits) if bits else "")
            + ". Growth that is bought can lift a pay target without the original business "
            "growing."
        )
    return None


def edit_plain(edit: Mapping[str, Any]) -> str:
    """Plain description of one material footnote edit."""
    note = str(edit.get("note") or "a").strip()
    kind = str(edit.get("kind") or "").upper()
    if kind == "INSERTED":
        s = f"A new sentence was added to the “{note}” section of the fine print."
    elif kind == "DELETED":
        s = f"A sentence was removed from the “{note}” section of the fine print."
    else:
        s = f"A sentence in the “{note}” section of the fine print was reworded."
    if edit.get("softened"):
        s += " The new wording is less definite (for example “will” became “may”)."
    m = _TERM.search(str(edit.get("explanation") or ""))
    if m:
        verb = "contained" if kind == "DELETED" else "contains"
        s += f" It {verb} “{m.group(1)}”, one of the accounting terms we watch for."
    return s


# ---------------------------------------------------------------------------
# Verdict (top of the company page)
# ---------------------------------------------------------------------------


_REASON_PLAIN: tuple[tuple[str, str], ...] = (
    (
        "receivables immaterial",
        "customers owe it less than 2% of a year's sales, too little to test",
    ),
    (
        "inventory immaterial",
        "its unsold goods are worth less than 2% of what it spends on the goods it sells in a "
        "year, too little to test",
    ),
    (
        "capitalized software immaterial",
        "the software spending it spreads over future years is under 1% of a year's sales, "
        "too little to test",
    ),
    (
        "no inventory tag",
        "we couldn't find a figure for unsold goods in the company's data in a form we can read",
    ),
    ("capitalized software not tagged", "we couldn't find this figure in the company's data"),
    ("financial issuer", "this check doesn't apply to banks, insurers and similar companies"),
    ("issuer", "this check doesn't apply to this kind of company"),
    ("≤ 0", "the figures don't allow a meaningful result"),
    ("4 discrete quarters required", "not enough quarters of data to cover a full year"),
    ("not tagged", "a figure the check needs isn't in the company's data"),
    ("unavailable", "a figure the check needs isn't in the company's data"),
)
_REASON_FALLBACK = "the figures needed aren't available"


def _reason_plain(reason: Any) -> str:
    """Plain reason for a check that could not run. Unknown engine text is never shown
    raw here; the technical reason stays on the Ratios tab."""
    low = str(reason or "").lower()
    for key, text in _REASON_PLAIN:
        if key in low:
            return text
    return _REASON_FALLBACK


def _no_data(check: Any) -> bool:
    try:
        return _has_no_data(check)
    except (AttributeError, TypeError):  # dict-form results (tests, fixtures)
        return False


@dataclass(frozen=True)
class Point:
    severity: str  # CRITICAL | WARNING | NO_FLAG
    title: str
    text: str
    view: str  # where the evidence is: "below", "pay" or "fine print" (story maps to a tab)
    analogy: str = ""


@dataclass(frozen=True)
class Verdict:
    headline: str
    summary: str
    points: tuple[Point, ...]
    good: tuple[str, ...]  # computed, scored, no flag, with data
    unchecked: tuple[str, ...]  # could not run (missing figures) or pay not readable
    empty: tuple[str, ...] = ()  # ran, but found nothing to examine (counts as run)
    not_applicable: tuple[str, ...] = ()  # left out for this company (engine NOT_APPLICABLE)


def headline_for(score: Any, label: Any = None) -> str:
    """Headline by the score-ring bands. It describes how many flags were raised, not
    whether the company's story is true."""
    if str(label or "").upper() == "NOT SCORED":
        return "We don't score this kind of company."
    s = _num(score)
    if s is None:
        return "Not enough data to give a score."
    if s >= 85:
        return "Only a few flags raised."
    if s >= 60:
        return "A mixed picture: some things worth a closer look."
    return "Many flags: several things worth a closer look."


def _scored_a(mods: Mapping[str, Any]) -> list[Any]:
    out = [
        f
        for f in as_list(get(mods.get("A"), "findings"))
        if str(get(f, "category")) == "accounting"
        and str(get(f, "severity")) in ("CRITICAL", "WARNING")
        and bool(get(get(f, "check"), "scored", True))
    ]
    rank = {"CRITICAL": 0, "WARNING": 1}
    out.sort(key=lambda f: (rank.get(str(get(f, "severity")), 9), str(get(f, "id", ""))))
    return out


def verdict(result: Any, edits: Sequence[Mapping[str, Any]] = ()) -> Verdict:
    """What a newcomer should take away, built only from scored results."""
    company = short_name(result)
    mods = get(result, "modules", {}) or {}
    if not isinstance(mods, Mapping):
        mods = {}
    score_obj = get(result, "score")
    score = get(score_obj, "score")
    label = get(score_obj, "label")

    points: list[Point] = []
    clashes = 0
    for f in _scored_a(mods):
        check = get(f, "check")
        cid = str(get(check, "check_id", ""))
        cp = CHECK_PLAIN.get(cid)
        if get(f, "contradiction", False):
            clashes += 1
        text = numbers_sentence(check, company) if check is not None else None
        # A flagged finding is never dropped: without a numbers sentence, say why it matters.
        body = f"{text} {cp.why}" if text and cp else (cp.why if cp else str(get(f, "title", "")))
        for a in as_list(get(check, "annotations")):
            p = annotation_plain(a)
            if p:
                body += " " + p
                break
        points.append(
            Point(
                str(get(f, "severity")),
                cp.name if cp else str(get(f, "title", "Flag")),
                body,
                "below",
                cp.analogy if cp else "",
            )
        )

    for f in as_list(get(mods.get("C"), "findings")):
        if str(get(f, "category")) != "incentives":
            continue
        text = incentive_plain(f)
        if text:
            sev = str(get(f, "severity"))
            points.append(Point(sev, "How executives are paid", text, "pay"))

    scored_edits = [e for e in edits if str(e.get("form")) == "10-K"]
    if scored_edits:
        notes: dict[str, int] = {}
        for e in scored_edits:
            key = str(e.get("note") or "other")
            notes[key] = notes.get(key, 0) + 1
        ranked = sorted(notes, key=lambda k: (-notes[k], k))
        n = len(scored_edits)
        if len(ranked) == 1:
            where = f", in \u201c{ranked[0]}\u201d"
        elif notes[ranked[0]] > notes[ranked[1]]:
            where = f", most in \u201c{ranked[0]}\u201d"
        else:
            shown = [f"\u201c{k}\u201d" for k in ranked[:3]]
            more = ", among others" if len(ranked) > 3 else ""
            where = ", in " + (", ".join(shown[:-1]) + " and " + shown[-1]) + more
        points.append(
            Point(
                "WARNING",
                "Edits to the fine print",
                f"{company} made {n} notable {'edit' if n == 1 else 'edits'} to the fine print "
                f"(the detailed notes) in its latest annual report compared with the year "
                f"before"
                + where
                + (
                    ". It is shown word by word." if n == 1 else ". Each one is shown word by word."
                ),
                "fine print",
            )
        )

    hedging = get(get(mods.get("B"), "extra", {}), "hedging")
    if isinstance(hedging, Mapping) and str(hedging.get("status")) == "FIRED":
        points.append(
            Point(
                "WARNING",
                "More cautious wording",
                f"{company}'s fine print uses noticeably more cautious words (such as "
                "\u201cmay\u201d or \u201ccould\u201d) than a year earlier.",
                "fine print",
            )
        )

    good: list[str] = []
    unchecked: list[str] = []
    empty: list[str] = []
    not_applicable: list[str] = []
    for k in as_list(get(mods.get("A"), "checks")):
        cid = str(get(k, "check_id", ""))
        cp = CHECK_PLAIN.get(cid)
        if not cid.startswith("A") or cp is None or not bool(get(k, "scored", True)):
            continue
        state = str(get(k, "state"))
        if state == "COMPUTED" and str(get(k, "severity")) == "NO_FLAG":
            if _no_data(k):
                empty.append(f"{cp.name} (no such figures found in the company's data)")
            else:
                good.append(cp.name)
        elif state == "NOT_APPLICABLE":
            not_applicable.append(f"{cp.name} ({_reason_plain(get(k, 'reason'))})")
        elif state == "NOT_COMPUTABLE":
            unchecked.append(f"{cp.name} ({_reason_plain(get(k, 'reason'))})")
    c_status = str(get(mods.get("C"), "status", ""))
    if c_status == "low_confidence":
        unchecked.append(
            "How executives are paid (we couldn't read the pay details reliably, so this part "
            "wasn't scored)"
        )
    elif c_status in ("unavailable", "error", "timeout"):
        unchecked.append("How executives are paid (we couldn't find a usable yearly pay report)")

    n_flags = len(points)
    if str(label or "").upper() == "NOT SCORED":
        summary = (
            "VERITAS doesn't give a score to this kind of company. Any results below are "
            "shown for reference only."
        )
    elif score is None:
        summary = (
            f"Too many of {company}'s figures were missing to score it fairly. Missing data is "
            "never counted as a pass."
        )
    else:
        n_big = sum(1 for p in points if p.severity == "CRITICAL")
        if n_flags:
            found = f", and raised {n_flags} {'flag' if n_flags == 1 else 'flags'}" + (
                f", including {n_big} big {'gap' if n_big == 1 else 'gaps'}." if n_big else "."
            )
            if clashes:
                found += (
                    f" In {clashes} of them, something managers said sits next to a number that "
                    "raised a flag for the same period."
                )
            else:
                found += (
                    " It isn't a direct clash between something managers said and the numbers; "
                    "it comes from the numbers, the fine print or pay on its own."
                    if n_flags == 1
                    else " None of them is a direct clash between something managers said and "
                    "the numbers; each comes from the numbers, the fine print or pay on its own."
                )
        else:
            found = ", and nothing stood out."
        summary = (
            f"{company} scores {int(score)} out of 100. We ran automated checks on its numbers, "
            "the fine print of its reports and how its executives are paid"
            + found
            + " A flag is a reason to look closer, not proof that anything is wrong."
        )
    return Verdict(
        headline_for(score, label),
        summary,
        tuple(points),
        tuple(good),
        tuple(unchecked),
        tuple(empty),
        tuple(not_applicable),
    )
