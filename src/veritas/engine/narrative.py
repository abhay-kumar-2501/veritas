"""Narrative claim extraction and pairing (PRD §7.2.3-§7.2.6).

Pure engine code. Claims are sentences inside a located section (`sections.Section`)
that match a claim cluster (Appendix B) and survive the negation, forward-looking
and boilerplate filters. Every claim's citation satisfies the §7.2.6 invariant:
`section.text[char_start:char_end] == quote_raw`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

from veritas.config import PARSER_VERSION, ti
from veritas.engine.html_text import is_data_row
from veritas.engine.sections import LABEL_8K, Section
from veritas.lexicons import (
    ABBREVIATION_RE,
    BOILERPLATE_PARA,
    CLUSTERS,
    DECIMAL_POINT,
    FORWARD,
    INTENT,
    INTENT_STRATEGY,
    NEGATION_SUFFIX,
    NEGATION_TOKEN,
    SENTENCE_SPLIT,
    TOKEN,
)
from veritas.models import CheckResult, Citation, Claim, ClaimCluster, DocRef, Finding, Severity

# §7.2.5 fixed pairing map.
PAIRING: dict[str, tuple[ClaimCluster, ...]] = {
    "A1_DSO": ("DEMAND",),
    "A2_DIO": ("DEMAND", "MARGIN"),
    "A3_ACCRUALS": (),
    "A4_CAPRATE": ("MARGIN",),
    "A5_RESTRUCT": ("ONETIME",),
}
ACQUISITION_DISTORTED = "acquisition-distorted"
MASK_CHAR = "\x00"  # same-length placeholder for masked periods
ELLIPSIS = "…"

CATEGORY_FLAG = "accounting"
CATEGORY_CONSISTENT = "claims_consistent"
CATEGORY_PRIOR = "accounting_prior_period"  # unscored, see docs/decisions/narrative.md

_FLAG_SEVERITIES = ("WARNING", "CRITICAL")


# ---------------------------------------------------------------------------
# Sentence splitting (§7.2.3)
# ---------------------------------------------------------------------------
def _mask(line: str) -> str:
    """Replace periods in abbreviations and decimals with a same-length placeholder so
    split offsets in the masked copy are valid offsets in the original."""
    chars = list(line)
    for m in ABBREVIATION_RE.finditer(line):
        for i in range(m.start(), m.end()):
            if chars[i] == ".":
                chars[i] = MASK_CHAR
    for m in DECIMAL_POINT.finditer(line):
        chars[m.start()] = MASK_CHAR
    return "".join(chars)


def _line_sentences(line: str) -> list[tuple[int, int]]:
    masked = _mask(line)
    spans: list[tuple[int, int]] = []
    start = 0
    for m in SENTENCE_SPLIT.finditer(masked):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(line)))
    out: list[tuple[int, int]] = []
    for s, e in spans:
        while s < e and line[s].isspace():
            s += 1
        while e > s and line[e - 1].isspace():
            e -= 1
        if e > s:
            out.append((s, e))
    return out


def _lines_with_offsets(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    pos = 0
    for line in text.split("\n"):
        out.append((pos, line))
        pos += len(line) + 1
    return out


def split_sentences(section_text: str) -> list[tuple[int, int]]:
    """Sentence spans (start, end) into `section_text`. Sentences never cross a line
    (block) boundary. No word-count filter is applied here."""
    spans: list[tuple[int, int]] = []
    for offset, line in _lines_with_offsets(section_text):
        for s, e in _line_sentences(line):
            spans.append((offset + s, offset + e))
    return spans


# ---------------------------------------------------------------------------
# Claim extraction (§7.2.4)
# ---------------------------------------------------------------------------
def _negated(sentence: str, match_start: int) -> bool:
    window = ti("NEGATION_WINDOW_TOKENS")
    tokens = TOKEN.findall(sentence[:match_start])[-window:] if window > 0 else []
    return any(NEGATION_TOKEN.match(tok) or tok.lower().endswith(NEGATION_SUFFIX) for tok in tokens)


def _intent_before(sentence: str, match_start: int) -> bool:
    """True when an INTENT marker starts before the cluster match (D-311)."""
    m = INTENT.search(sentence)
    return m is not None and m.start() < match_start


def _cluster_hit(sentence: str, cluster: str) -> bool:
    return any(
        not _negated(sentence, m.start()) and not _intent_before(sentence, m.start())
        for m in CLUSTERS[cluster].finditer(sentence)
    )


def extract_claims(section: Section, doc: DocRef) -> tuple[Claim, ...]:
    """Claims in a located section. `doc.report_date` is the claim period (for an 8-K
    the caller passes the paired periodic filing's period). No period -> no claims."""
    if section.status not in ("OK", "TRUNCATED") or not section.text:
        return ()
    if doc.report_date is None:
        return ()
    label = section.label or LABEL_8K
    min_words = ti("SENTENCE_MIN_WORDS")
    max_words = ti("SENTENCE_MAX_WORDS")
    text = section.text
    claims: list[Claim] = []
    for offset, line in _lines_with_offsets(text):
        if not line.strip() or is_data_row(line) or BOILERPLATE_PARA.search(line):
            continue
        for s, e in _line_sentences(line):
            sentence = line[s:e]
            n = len(sentence.split())
            if n < min_words or n > max_words:
                continue
            if FORWARD.search(sentence) or INTENT_STRATEGY.search(sentence):
                continue  # tagged FORWARD / INTENT (strategy noun), never a claim (D-311)
            for cluster in ("DEMAND", "MARGIN", "ONETIME"):
                if not _cluster_hit(sentence, cluster):
                    continue
                start, end = offset + s, offset + e
                citation = doc.cite(
                    section=label,
                    parser_version=PARSER_VERSION,
                    quote_raw=text[start:end],
                    char_start=start,
                    char_end=end,
                )
                claims.append(Claim(cluster=cluster, citation=citation, period_end=doc.report_date))
    return tuple(claims)


def display_quote(q: str) -> str:
    """Display-only truncation: > QUOTE_DISPLAY_MAX_WORDS words -> cut at a word
    boundary with an ellipsis. The cited quote_raw is never modified."""
    limit = ti("QUOTE_DISPLAY_MAX_WORDS")
    words = q.split()
    if len(words) <= limit:
        return q
    return " ".join(words[:limit]) + ELLIPSIS


# ---------------------------------------------------------------------------
# Pairing (§7.2.5)
# ---------------------------------------------------------------------------
def _is_flag(check: CheckResult) -> bool:
    return check.scored and check.state == "COMPUTED" and check.severity in _FLAG_SEVERITIES


def _is_8k(citation: Citation) -> bool:
    """Earnings-release citation: form "8-K" or an exhibit type "EX-99*", or the 8-K label."""
    form = citation.form.upper()
    return form.startswith("8-K") or form.startswith("EX-99") or citation.section == LABEL_8K


NO_DATA_MARKERS = ("no restructuring tags reported",)


def _has_no_data(check: CheckResult) -> bool:
    """True when a NO_FLAG result reflects absent data rather than measured numbers:
    a known no-data note in reason/annotations, or every input is None (D-313)."""
    notes = " ".join((check.reason or "", *check.annotations)).lower()
    if any(marker in notes for marker in NO_DATA_MARKERS):
        return True
    if all(v is None for v in check.inputs.values()):
        return True
    # A5: restructuring inputs absent and no quarter in the window had restructuring data
    # (NVDA/WMT seeds report Revenue_q but never a restructuring value).
    restruct = [v for k, v in check.inputs.items() if k.startswith("Restructuring")]
    return (
        bool(restruct)
        and all(v is None for v in restruct)
        and check.outputs.get("quarters_with_data_last8") == 0
    )


def _select_quotes(claims: Sequence[Claim]) -> tuple[Claim, ...]:
    """At most MAX_QUOTES_PER_CHECK distinct quotes: MD&A before 8-K, then earliest."""
    ordered = sorted(
        claims,
        key=lambda c: (
            1 if _is_8k(c.citation) else 0,
            c.citation.char_start if c.citation.char_start is not None else 0,
            c.citation.filing_date,
            c.citation.accession,
            c.citation.doc_name,
            c.cluster,
        ),
    )
    out: list[Claim] = []
    seen: set[tuple[str, str, int | None, int | None]] = set()
    for c in ordered:
        cit = c.citation
        key = (cit.accession, cit.doc_name, cit.char_start, cit.char_end)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= ti("MAX_QUOTES_PER_CHECK"):
            break
    return tuple(out)


def _escalate(check: CheckResult, has_claims: bool) -> tuple[Severity, bool]:
    base: Severity = check.severity if check.severity is not None else "NO_FLAG"
    if not has_claims or base not in _FLAG_SEVERITIES:
        return base, False
    if base == "WARNING" and ACQUISITION_DISTORTED not in check.annotations:
        return "CRITICAL", True
    return base, True


def _clusters_text(claims: Sequence[Claim]) -> str:
    return ", ".join(sorted({c.cluster for c in claims}))


def _contradiction(
    check: CheckResult, period: date, claims: tuple[Claim, ...], category: str, kind: str
) -> Finding:
    severity, contradiction = _escalate(check, True)
    numeric = check.severity or "NO_FLAG"
    parts = [
        f"Management narrative ({_clusters_text(claims)}) for the period ending "
        f"{period.isoformat()} is paired with a {numeric} numeric signal on {check.title}."
    ]
    if severity != numeric:
        parts.append(f"Severity escalated {numeric} -> {severity} because a paired claim exists.")
    elif ACQUISITION_DISTORTED in check.annotations:
        parts.append("Not escalated: the check is annotated acquisition-distorted.")
    if category == CATEGORY_PRIOR:
        parts.append("Prior period: shown for context, scored only at the latest period.")
    return Finding(
        id=f"A:{check.check_id}:{period.isoformat()}:{kind}",
        module="A",
        category=category,
        severity=severity,
        title=f"{check.title}: narrative claim vs. numbers",
        explanation=" ".join(parts),
        check=check,
        claims=claims,
        contradiction=contradiction,
        citations=tuple(c.citation for c in claims) + check.citations,
    )


def _unaddressed(check: CheckResult, period: date) -> Finding:
    severity: Severity = check.severity if check.severity is not None else "NO_FLAG"
    return Finding(
        id=f"A:{check.check_id}:{period.isoformat()}:unaddressed",
        module="A",
        category=CATEGORY_FLAG,
        severity=severity,
        title=f"Unaddressed divergence: {check.title}",
        explanation=(
            f"{severity} numeric signal on {check.title} for the period ending "
            f"{period.isoformat()}; no paired narrative claim was found in the located sections."
        ),
        check=check,
        claims=(),
        contradiction=False,
        citations=check.citations,
    )


def _consistent(check: CheckResult, period: date, claims: tuple[Claim, ...]) -> Finding:
    return Finding(
        id=f"A:{check.check_id}:{period.isoformat()}:consistent",
        module="A",
        category=CATEGORY_CONSISTENT,
        severity="NO_FLAG",
        title=f"Claims consistent with numbers: {check.title}",
        explanation=(
            f"Management narrative ({_clusters_text(claims)}) for the period ending "
            f"{period.isoformat()}; {check.title} shows no numeric flag (informational, unscored)."
        ),
        check=check,
        claims=claims,
        contradiction=False,
        citations=tuple(c.citation for c in claims) + check.citations,
    )


def pair_findings(
    checks_by_period: Callable[[str, date], CheckResult | None],
    claims: Sequence[Claim],
    latest_checks: Sequence[CheckResult],
) -> tuple[Finding, ...]:
    """Pair claims with checks (§7.2.5) and emit Module A findings.

    - Latest scored COMPUTED checks at WARNING/CRITICAL produce one `accounting`
      finding each: a contradiction (escalated unless acquisition-distorted) when a
      paired claim exists for the same period, otherwise "Unaddressed divergence".
    - Claims pair only with the check evaluated at their own period P
      (`checks_by_period(check_id, P)`). A NO_FLAG check yields an informational
      `claims_consistent` finding; a flagged check at a non-latest period yields an
      unscored `accounting_prior_period` contradiction.
    """
    latest_by_id = {c.check_id: c for c in latest_checks}
    findings: list[Finding] = []

    for check_id in sorted(PAIRING):
        clusters = PAIRING[check_id]
        latest = latest_by_id.get(check_id)
        by_period: dict[date, list[Claim]] = {}
        for claim in claims:
            if claim.cluster in clusters:
                by_period.setdefault(claim.period_end, []).append(claim)

        if latest is not None and _is_flag(latest) and latest.period_end is not None:
            paired = _select_quotes(by_period.get(latest.period_end, []))
            if paired:
                period = latest.period_end
                findings.append(
                    _contradiction(latest, period, paired, CATEGORY_FLAG, "contradiction")
                )
            else:
                findings.append(_unaddressed(latest, latest.period_end))

        for period in sorted(by_period):
            if latest is not None and latest.period_end == period:
                check: CheckResult | None = latest
            else:
                check = checks_by_period(check_id, period)
            if check is None or check.period_end != period:
                continue  # period alignment: never pair across periods
            if not check.scored or check.state != "COMPUTED":
                continue
            selected = _select_quotes(by_period[period])
            if check.severity == "NO_FLAG":
                if not _has_no_data(check):
                    findings.append(_consistent(check, period, selected))
            elif _is_flag(check) and check is not latest:
                findings.append(_contradiction(check, period, selected, CATEGORY_PRIOR, "prior"))

    kind_rank = {"contradiction": 0, "unaddressed": 1, "prior": 2, "consistent": 3}
    return tuple(sorted(findings, key=lambda f: (kind_rank[f.id.rsplit(":", 1)[1]], f.id)))
