"""Filing selection from a submissions payload (PRD §6.4)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from veritas.config import ti
from veritas.edgar.source import DOCS_KEY, DataSource, document_url
from veritas.models import DocRef

TEN_K_FORMS = ("10-K", "10-KT")
MAX_8K_CANDIDATES = 3
_HTML_RE = re.compile(r"\.html?$", re.I)
_EX991_NAME_RE = re.compile(r"ex-?99[._-]?1", re.I)
_EX99_NAME_RE = re.compile(r"ex-?99", re.I)
_EX13_NAME_RE = re.compile(r"ex-?13", re.I)

REQUIRED_COLUMNS = (
    "form",
    "accessionNumber",
    "filingDate",
    "reportDate",
    "primaryDocument",
    "items",
)


@dataclass(frozen=True)
class Filing:
    """One row of filings.recent."""

    form: str
    accession: str
    filing_date: date
    report_date: date | None
    primary_doc: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class FilingSelection:
    cik: int
    ticker: str
    as_of: date
    sic: str | None
    fiscal_year_end: str | None  # "MMDD"
    latest_10k: DocRef | None
    prior_10k: DocRef | None
    ten_qs: tuple[DocRef, ...]  # newest first, up to 4
    q_pair: tuple[DocRef, DocRef] | None  # (latest 10-Q, same quarter one year earlier)
    proxy: DocRef | None
    earnings_8k: dict[str, DocRef] = field(default_factory=dict)  # periodic accession -> EX-99.1
    amendment_notice: str | None = None
    unsupported_reason: str | None = None
    notes: tuple[str, ...] = ()  # explicit reason for every missing item


def _d(s: Any) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def parse_filings(submissions: dict[str, Any]) -> list[Filing]:
    """Columnar `filings.recent` -> rows, newest first (stable on ties)."""
    recent = submissions.get("filings", {}).get("recent", {})
    missing = [c for c in REQUIRED_COLUMNS if c not in recent]
    if missing:
        raise KeyError(f"submissions.filings.recent lacks columns: {missing}")
    n = len(recent["form"])
    rows: list[Filing] = []
    for i in range(n):
        fd = _d(recent["filingDate"][i])
        if fd is None:
            continue
        rows.append(
            Filing(
                form=recent["form"][i],
                accession=recent["accessionNumber"][i],
                filing_date=fd,
                report_date=_d(recent["reportDate"][i]),
                primary_doc=recent["primaryDocument"][i] or "",
                items=tuple(x.strip() for x in (recent["items"][i] or "").split(",") if x.strip()),
            )
        )
    rows.sort(key=lambda r: r.filing_date, reverse=True)
    return rows


def find_exhibit(
    index_json: dict[str, Any] | None, type_prefix: str, name_regex: re.Pattern[str] | None = None
) -> str | None:
    """File name of the first HTML exhibit whose document type starts with `type_prefix`
    (e.g. "EX-13", "EX-99"). Types come from the `_veritas_documents` list (parsed from the
    filing's index-headers page; index.json's own `type` is an icon name). Falls back to
    `name_regex` over index.json file names."""
    if not index_json:
        return None
    pre = type_prefix.upper()
    docs = sorted(
        (d for d in index_json.get(DOCS_KEY, []) or []),
        key=lambda d: (d.get("seq") is None, d.get("seq") or 0),
    )
    for d in docs:
        if str(d.get("type", "")).upper().startswith(pre) and _HTML_RE.search(d.get("name", "")):
            return str(d["name"])
    for it in index_json.get("directory", {}).get("item", []):  # type field, if ever real
        if str(it.get("type", "")).upper().startswith(pre) and _HTML_RE.search(it.get("name", "")):
            return str(it["name"])
    if name_regex is not None:
        for it in index_json.get("directory", {}).get("item", []):
            nm = str(it.get("name", ""))
            if _HTML_RE.search(nm) and name_regex.search(nm):
                return nm
    return None


def find_earnings_exhibit(index_json: dict[str, Any] | None) -> str | None:
    """EX-99.1, else first EX-99*, else file name matching /ex-?99[._-]?1/i (PRD §6.4)."""
    if not index_json:
        return None
    docs = index_json.get(DOCS_KEY, []) or []
    if any(str(d.get("type", "")).upper() == "EX-99.1" for d in docs):
        for d in sorted(docs, key=lambda d: d.get("seq") or 0):
            if str(d.get("type", "")).upper() == "EX-99.1" and _HTML_RE.search(d.get("name", "")):
                return str(d["name"])
    return find_exhibit(index_json, "EX-99", _EX991_NAME_RE)


def find_ex13(index_json: dict[str, Any] | None) -> str | None:
    return find_exhibit(index_json, "EX-13", _EX13_NAME_RE)


def exhibit_docref(parent: DocRef, form: str, name: str) -> DocRef:
    return DocRef(
        cik=parent.cik,
        ticker=parent.ticker,
        form=form,
        accession=parent.accession,
        filing_date=parent.filing_date,
        report_date=parent.report_date,
        doc_name=name,
        url=document_url(parent.cik, parent.accession, name),
    )


def _docref(f: Filing, cik: int, ticker: str, notes: list[str], label: str) -> DocRef | None:
    if not f.primary_doc or not _HTML_RE.search(f.primary_doc):
        shown = f.primary_doc or "(none)"
        notes.append(
            f"{label} {f.accession}: primary document {shown} is not .htm/.html: "
            "document unavailable"
        )
        return None
    return DocRef(
        cik=cik,
        ticker=ticker,
        form=f.form,
        accession=f.accession,
        filing_date=f.filing_date,
        report_date=f.report_date,
        doc_name=f.primary_doc,
        url=document_url(cik, f.accession, f.primary_doc),
    )


def _as_of(ds: DataSource, as_of: date | None) -> date:
    if as_of is not None:
        return as_of
    fa = ds.fetched_at
    if fa is not None:
        d: date = fa.astimezone(UTC).date()
        return d
    return datetime.now(UTC).date()


def _months_before(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    month = m + 1
    day = min(
        d.day,
        [
            31,
            29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ][m],
    )
    return date(y, month, day)


def select_filings(
    submissions: dict[str, Any],
    cik: int,
    ticker: str,
    ds: DataSource,
    as_of: date | None = None,
) -> FilingSelection:
    ref = _as_of(ds, as_of)
    rows = parse_filings(submissions)
    notes: list[str] = []

    sic = submissions.get("sic") or None
    fye = submissions.get("fiscalYearEnd") or None
    if sic is None:
        notes.append("submissions has no sic")
    if fye is None:
        notes.append("submissions has no fiscalYearEnd")

    # --- 10-K ---------------------------------------------------------------
    tenks = [r for r in rows if r.form in TEN_K_FORMS]
    latest_10k = _docref(tenks[0], cik, ticker, notes, "latest 10-K") if tenks else None
    prior_10k = _docref(tenks[1], cik, ticker, notes, "prior 10-K") if len(tenks) > 1 else None
    unsupported: str | None = None
    cutoff = ref - timedelta(days=365 * ti("FILER_LOOKBACK_YEARS"))
    if not tenks or tenks[0].filing_date < cutoff:
        unsupported = "Unsupported filer type in v1: no 10-K filed in the last 3 years"
    if not tenks:
        notes.append("no 10-K in submissions")
    elif len(tenks) < 2:
        notes.append("no prior 10-K in submissions")

    amendment_notice: str | None = None
    if tenks:
        newer = [r for r in rows if r.form == "10-K/A" and r.filing_date > tenks[0].filing_date]
        if newer:
            a = newer[0]
            amendment_notice = (
                f"A 10-K/A (accession {a.accession}) was filed on {a.filing_date.isoformat()}, "
                f"after the 10-K analyzed (accession {tenks[0].accession}); "
                "amended content is not analyzed."
            )

    # --- 10-Qs ----------------------------------------------------------------
    qs_all = [r for r in rows if r.form == "10-Q"]
    ten_qs = tuple(
        d for r in qs_all[:4] if (d := _docref(r, cik, ticker, notes, "10-Q")) is not None
    )
    q_pair: tuple[DocRef, DocRef] | None = None
    if qs_all:
        latest_q = qs_all[0]
        if latest_q.report_date is None:
            notes.append("latest 10-Q has no reportDate: 10-Q pair unavailable")
        else:
            tol = ti("YOY_MATCH_TOL_DAYS")
            prior_q = next(
                (
                    r
                    for r in qs_all[1:]
                    if r.report_date is not None
                    and abs((latest_q.report_date - r.report_date).days - 365) <= tol
                ),
                None,
            )
            if prior_q is None:
                notes.append(
                    "no 10-Q for the same quarter one year earlier (reportDate 365 +/- 10 days)"
                )
            else:
                pa = _docref(latest_q, cik, ticker, notes, "10-Q pair (latest)")
                pb = _docref(prior_q, cik, ticker, notes, "10-Q pair (prior year)")
                if pa is not None and pb is not None:
                    q_pair = (pa, pb)
    else:
        notes.append("no 10-Q in submissions")

    # --- Proxy ------------------------------------------------------------------
    proxy: DocRef | None = None
    proxies = [r for r in rows if r.form == "DEF 14A"]
    pcut = _months_before(ref, ti("PROXY_MAX_AGE_MONTHS"))
    if proxies and proxies[0].filing_date >= pcut:
        proxy = _docref(proxies[0], cik, ticker, notes, "DEF 14A")
    elif proxies:
        notes.append(
            f"newest DEF 14A ({proxies[0].filing_date.isoformat()}) is older than "
            f"{ti('PROXY_MAX_AGE_MONTHS')} months"
        )
    else:
        notes.append("no DEF 14A in submissions")

    # --- Earnings 8-Ks ------------------------------------------------------------
    earnings: dict[str, DocRef] = {}
    eights = [r for r in rows if r.form == "8-K" and "2.02" in r.items]
    grace = timedelta(days=ti("EARNINGS_8K_GRACE_DAYS"))
    for label, periodic in (
        ("latest 10-K", latest_10k),
        ("latest 10-Q", ten_qs[0] if ten_qs else None),
    ):
        if periodic is None:
            continue
        if periodic.report_date is None:
            notes.append(f"{label}: no reportDate, earnings 8-K window undefined")
            continue
        lo, hi = periodic.report_date, periodic.filing_date + grace
        cands = [r for r in eights if lo <= r.filing_date <= hi][:MAX_8K_CANDIDATES]
        found = False
        for c in cands:
            idx = ds.filing_index(cik, c.accession)
            name = find_earnings_exhibit(idx)
            if name:
                eight_k = DocRef(
                    cik=cik, ticker=ticker, form="EX-99.1", accession=c.accession,
                    filing_date=c.filing_date, report_date=None, doc_name=name,
                    url=document_url(cik, c.accession, name),
                )  # fmt: skip
                earnings[periodic.accession] = eight_k
                found = True
                break
        if not found:
            notes.append(f"{label}: no earnings press release (EX-99.1) found in the 8-K window")

    return FilingSelection(
        cik=cik,
        ticker=ticker,
        as_of=ref,
        sic=str(sic) if sic is not None else None,
        fiscal_year_end=str(fye) if fye is not None else None,
        latest_10k=latest_10k,
        prior_10k=prior_10k,
        ten_qs=ten_qs,
        q_pair=q_pair,
        proxy=proxy,
        earnings_8k=earnings,
        amendment_notice=amendment_notice,
        unsupported_reason=unsupported,
        notes=tuple(notes),
    )
