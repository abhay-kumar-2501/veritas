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

from veritas.engine.narrative import _has_no_data
from veritas.ui.components import as_list, fmt_money, get, human_reason

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
    "MARGIN": "improving margins or efficiency",
    "ONETIME": "costs being one-time",
}


@dataclass(frozen=True)
class CheckPlain:
    name: str  # what a newcomer would call it
    question: str  # the question the check answers
    why: str  # why a gap matters
    analogy: str  # everyday comparison


CHECK_PLAIN: dict[str, CheckPlain] = {
    "A1_DSO": CheckPlain(
        "Days to get paid",
        "How many days of sales do customers still owe the company?",
        "If what customers owe grows faster than sales, reported sales can be running ahead "
        "of the cash actually coming in.",
        "Like a shop where more customers each month say “put it on my tab”.",
    ),
    "A2_DIO": CheckPlain(
        "Days of unsold stock",
        "How many days of sales is sitting in the warehouse?",
        "Stock piling up faster than it sells can mean demand is cooling, or that some of "
        "it may later have to be marked down.",
        "Like a bakery baking more loaves each week than it sells.",
    ),
    "A3_ACCRUALS": CheckPlain(
        "Profit vs. actual cash",
        "Does the profit the company reports turn into real cash?",
        "Profit is an accounting figure that includes amounts not yet received; cash is what "
        "actually arrives. A gap means part of the profit has not yet shown up as cash.",
        "Like a payslip that shows more than what actually lands in your bank account.",
    ),
    "A4_CAPRATE": CheckPlain(
        "Software costs recorded as assets",
        "Is the company recording more of its software development spending as a long-term asset?",
        "Software spending recorded as an asset is spread over future years instead of "
        "reducing this year's profit, so a rising share lifts current profit. The treatment "
        "is allowed; the check watches the trend.",
        "Like spreading the cost of a new laptop over three years instead of counting it all "
        "today.",
    ),
    "A5_RESTRUCT": CheckPlain(
        "Repeated restructuring costs",
        "Do supposedly one-off restructuring costs show up again and again?",
        "Costs that recur every year are part of the normal cost of doing business, even "
        "when they are labelled one-time.",
        "Like a “one-off” car repair you pay for every few months.",
    ),
    "X_CAPEX_DA": CheckPlain(
        "Investment vs. wear and tear",
        "Is the company spending more on equipment than it wears out?",
        "Shown for context only.",
        "Like comparing what you spend on a house with how fast it ages.",
    ),
    "X_DPO": CheckPlain(
        "Days to pay suppliers",
        "How long does the company take to pay its own bills?",
        "Paying suppliers later boosts cash in the short term. Shown for context only.",
        "Like paying your credit card later to keep cash in hand.",
    ),
    "X_FCF": CheckPlain(
        "Free cash flow",
        "How much cash is left after running and investing in the business?",
        "Shown for context only.",
        "Like your take-home pay minus the cost of maintaining your home.",
    ),
    "X_FCF_SBC": CheckPlain(
        "Free cash flow after stock pay",
        "How much cash is left once you count staff paid in shares as a real cost?",
        "Shown for context only.",
        "Like counting gift cards you hand out as part of your spending.",
    ),
    "X_ROIC": CheckPlain(
        "Return on money invested",
        "How much profit does each dollar invested in the business earn?",
        "Shown for context only.",
        "Like the yearly return on money you put into a small business.",
    ),
    "X_RESTATED": CheckPlain(
        "Revised past figures",
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
        trend = _trend(cur, prior, unit="days")
        return (
            f"At the end of {when}, {company}'s customers owed it the equivalent of about "
            f"{_days(cur)} of sales, {trend} a year earlier ({_days(prior)})."
        )
    if cid == "A2_DIO":
        cur, prior = _num(outputs.get("dio")), _num(outputs.get("dio_prior_year"))
        if cur is None or prior is None:
            return None
        trend = _trend(cur, prior, unit="days")
        return (
            f"At the end of {when}, {company} held about {_days(cur)}' worth of stock "
            f"(measured at cost), {trend} a year earlier ({_days(prior)})."
        )
    if cid == "A3_ACCRUALS":
        ni, ocf = _num(inputs.get("NI_ttm")), _num(inputs.get("OCF_ttm"))
        if ni is None or ocf is None:
            return None
        base = (
            f"Over the 12 months to {when}, {company} reported {fmt_money(ni)} of profit "
            f"and brought in {fmt_money(ocf)} of cash from running the business"
        )
        ar = _num(outputs.get("accrual_ratio"))
        size = (
            f" The gap equals about {ar * 100:,.0f}% of the company's average total assets."
            if ar is not None and ar > 0 and ocf < ni
            else ""
        )
        if ni > 0:
            cents = ocf / ni * 100
            if cents < 100:
                return base + f": about {cents:,.0f}\u00a2 of cash for every $1 of profit." + size
            return base + ": more cash than profit."
        return base + "." + size
    if cid == "A5_RESTRUCT":
        n4 = _num(outputs.get("material_quarters_last4"))
        if n4 is None:
            return None
        q = "quarter" if round(n4) == 1 else "quarters"
        return (
            f"In the last four quarters to {when}, {company} booked significant "
            f"restructuring costs in {n4:,.0f} {q}."
        )
    return None


def _trend(cur: float, prior: float, unit: str) -> str:
    """Change between the two values as displayed (rounded), so the arithmetic on screen
    adds up; the unit is singular for 1."""
    diff = round(cur) - round(prior)
    if diff == 0:
        return "about the same as"
    word = unit[:-1] if abs(diff) == 1 and unit.endswith("s") else unit
    return f"{'up' if diff > 0 else 'down'} {abs(diff):,} {word} from"


_ANNOT_PLAIN: dict[str, str] = {
    "acquisition-distorted": "Goodwill or spending on acquisitions jumped over the past year "
    "(usually a sign of buying other businesses), which can distort this measure; read it "
    "with extra caution.",
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
        "Earlier period, not scored. "
        if str(get(finding, "category", "")) == "accounting_prior_period"
        else ""
    )
    if get(finding, "contradiction", False) and topic:
        return (
            f"{prefix}Management pointed to {topic}, while this measure moved in a direction "
            f"that can sit uneasily with that: {_lower_first(numbers)}"
        )
    if sev in ("CRITICAL", "WARNING") and not claims:
        cid = str(get(check, "check_id", ""))
        if cid == "A3_ACCRUALS":  # no statement type is ever paired with this check
            return prefix + numbers
        return (
            f"{prefix}{numbers} Our text scan did not pair a management statement with this "
            "measure (it only looks for certain kinds of statements, such as claims about "
            "demand or margins)."
        )
    if sev == "NO_FLAG" and topic:
        return (
            f"Management pointed to {topic}, and this measure raised no flag: "
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
        parts = []
        m = _SBC_FCF.search(expl)
        if m:
            sbc, fcf = _money_value(m.group(1)), _money_value(m.group(2))
            if sbc is not None and fcf is not None and fcf > 0:
                cmp = "more than" if sbc > fcf else f"equal to {sbc / fcf * 100:,.0f}% of"
                parts.append(
                    f"{m.group(1)} of stock-based pay to all employees, {cmp} the "
                    f"{m.group(2)} of free cash flow the business produced"
                )
        m = _SBC_REV.search(expl)
        if m:
            parts.append(f"{m.group(1)} of stock-based pay, equal to {m.group(2)} of revenue")
        tail = (
            " Over the last 12 months the company recorded " + "; and ".join(parts) + "."
            if parts
            else ""
        )
        return (
            "Part of executives' pay depends on an adjusted (\u201cnon-GAAP\u201d) figure, one "
            "the company defines itself." + tail + " Some adjusted figures leave stock-based "
            "pay out; we did not check what this company's figure excludes."
        )
    if fid == "C2":
        m = _GW.search(expl)
        bits = []
        if "rose" in expl:
            bits.append("its share of assets rose over the past year")
        if "acquisition guard" in expl:
            bits.append("goodwill or acquisition spending jumped recently")
        share = f" Goodwill makes up {m.group(1)} of assets" if m else " Goodwill is large"
        return (
            "Executives are partly paid for revenue or deal-driven growth, and the company "
            "carries a lot of goodwill (the premium paid for businesses it bought)."
            + share
            + (", and " + " and ".join(bits) if bits else "")
            + ". Growth that is bought can lift a pay metric without the existing business "
            "growing."
        )
    return None


def edit_plain(edit: Mapping[str, Any]) -> str:
    """Plain description of one material footnote edit."""
    note = str(edit.get("note") or "a").strip()
    kind = str(edit.get("kind") or "").upper()
    if kind == "INSERTED":
        s = f"A new sentence was added to the “{note}” note."
    elif kind == "DELETED":
        s = f"A sentence was removed from the “{note}” note."
    else:
        s = f"A sentence in the “{note}” note was reworded."
    if edit.get("softened"):
        s += " The new wording is less definite (for example “will” became “may”)."
    m = _TERM.search(str(edit.get("explanation") or ""))
    if m:
        s += f" It contains “{m.group(1)}”, one of the accounting terms we watch for."
    return s


# ---------------------------------------------------------------------------
# Verdict (top of the company page)
# ---------------------------------------------------------------------------


_REASON_PLAIN: tuple[tuple[str, str], ...] = (
    ("immaterial", "customer balances are under 2% of yearly sales, too small to test"),
    ("no inventory tag", "the inventory figure isn't reported under a standard tag we can read"),
    ("capitalized software not tagged", "the company doesn't report this figure separately"),
)


def _reason_plain(reason: Any) -> str:
    low = str(reason or "").lower()
    for key, text in _REASON_PLAIN:
        if key in low:
            return text
    return human_reason(reason) or "the figures needed aren't reported"


@dataclass(frozen=True)
class Point:
    severity: str  # CRITICAL | WARNING | NO_FLAG
    title: str
    text: str
    view: str  # which tab shows the evidence
    analogy: str = ""


@dataclass(frozen=True)
class Verdict:
    headline: str
    summary: str
    points: tuple[Point, ...]
    good: tuple[str, ...]
    unchecked: tuple[str, ...]


def headline_for(score: Any) -> str:
    s = _num(score)
    if s is None:
        return "Not enough data to give a score."
    if s >= 85:
        return "The story and the numbers largely line up."
    if s >= 60:
        return "A mixed picture: some things worth a closer look."
    return "Several places where the story and the numbers pull apart."


def _scored_a(mods: Mapping[str, Any]) -> list[Any]:
    out = [
        f
        for f in as_list(get(mods.get("A"), "findings"))
        if str(get(f, "category")) == "accounting"
        and str(get(f, "severity")) in ("CRITICAL", "WARNING")
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

    points: list[Point] = []
    for f in _scored_a(mods):
        check = get(f, "check")
        cid = str(get(check, "check_id", ""))
        cp = CHECK_PLAIN.get(cid)
        text = numbers_sentence(check, company) if check is not None else None
        if cp is None or text is None:
            continue
        extra = ""
        for a in as_list(get(check, "annotations")):
            p = annotation_plain(a)
            if p:
                extra = " " + p
                break
        points.append(
            Point(
                str(get(f, "severity")),
                cp.name,
                f"{text} {cp.why}{extra}",
                "below",
                cp.analogy,
            )
        )

    for f in as_list(get(mods.get("C"), "findings")):
        if str(get(f, "category")) != "incentives":
            continue
        text = incentive_plain(f)
        if text:
            sev = str(get(f, "severity"))
            points.append(Point(sev, "How executives are paid", text, "the Proxy tab"))

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
                f"{company} made {n} notable {'edit' if n == 1 else 'edits'} to the notes in "
                f"its latest annual report compared with the year before"
                + where
                + (
                    ". It is shown word by word." if n == 1 else ". Each one is shown word by word."
                ),
                "the Redline tab",
            )
        )

    hedging = get(get(mods.get("B"), "extra", {}), "hedging")
    if isinstance(hedging, Mapping) and str(hedging.get("status")) == "FIRED":
        points.append(
            Point(
                "WARNING",
                "More cautious wording",
                f"{company}'s notes use noticeably more hedge words (such as “may” "
                "or “could”) than a year earlier.",
                "the Redline tab",
            )
        )

    good: list[str] = []
    unchecked: list[str] = []
    for k in as_list(get(mods.get("A"), "checks")):
        cid = str(get(k, "check_id", ""))
        cp = CHECK_PLAIN.get(cid)
        if not cid.startswith("A") or cp is None:
            continue
        state = str(get(k, "state"))
        if state == "COMPUTED" and str(get(k, "severity")) == "NO_FLAG":
            if _has_no_data(k):
                unchecked.append(f"{cp.name} (nothing reported to check)")
            else:
                good.append(cp.name)
        elif state in ("NOT_COMPUTABLE", "NOT_APPLICABLE"):
            unchecked.append(f"{cp.name} ({_reason_plain(get(k, 'reason'))})")
    c_status = str(get(mods.get("C"), "status", ""))
    if c_status == "low_confidence":
        unchecked.append(
            "How executives are paid (the pay details could not be read reliably, so nothing "
            "was scored)"
        )
    elif c_status in ("unavailable", "error", "timeout"):
        unchecked.append("How executives are paid (no usable proxy statement)")

    n_flags = len(points)
    if score is None:
        summary = (
            f"Too many of {company}'s figures were missing to score it fairly. Missing data is "
            "never counted as a pass."
        )
    else:
        thing = "thing" if n_flags == 1 else "things"
        summary = (
            f"{company} scores {int(score)} out of 100. We compared what its managers wrote "
            "with the numbers it reported to the SEC"
            + (
                f" and found {n_flags} {thing} worth a closer look."
                if n_flags
                else " and nothing stood out."
            )
            + " A flag is a reason to read further, not proof that anything is wrong."
        )
    return Verdict(headline_for(score), summary, tuple(points), tuple(good), tuple(unchecked))
