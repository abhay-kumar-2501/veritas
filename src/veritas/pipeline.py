"""`analyze()`: the single orchestration entry point (PRD §5.1).

resolve → submissions → filing selection → companyfacts → normalize → gating
→ Module A numeric → narrative extraction & pairing → Module B → Module C → score.

Each module runs inside try/except plus a timeout. A failure becomes a
`ModuleResult(status="error"|"timeout")` and never crashes the pipeline.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from typing import Any, TypeVar

from veritas.config import PARSER_VERSION, SEED_TICKERS, get_settings, t
from veritas.edgar.filings import FilingSelection, exhibit_docref, find_ex13, select_filings
from veritas.edgar.resolve import CompanyRef, TickerNotFound, normalize_ticker, resolve
from veritas.edgar.source import DataSource, SeedSource, get_source
from veritas.engine.accounting_checks import (
    AccountingOutput,
    evaluate_check,
    latest_period,
    run_accounting,
)
from veritas.engine.footnote_diff import run_footnotes
from veritas.engine.gating import Gating, gate
from veritas.engine.html_text import html_to_text
from veritas.engine.narrative import extract_claims, pair_findings
from veritas.engine.proxy_xray import run_proxy
from veritas.engine.scorer import score
from veritas.engine.sections import Section, locate_earnings_release, locate_mda, needs_ex13
from veritas.engine.xbrl_normalize import NormalizedFacts, normalize
from veritas.models import (
    AnalysisResult,
    CheckResult,
    Claim,
    DocRef,
    ModStatus,
    ModuleResult,
)

log = logging.getLogger(__name__)

__all__ = [
    "STAGES",
    "AnalysisError",
    "NoXBRL",
    "TickerNotFound",
    "UnsupportedFiler",
    "analyze",
]

STAGES: tuple[str, ...] = (
    "Resolve CIK",
    "Fetch submissions",
    "Fetch company facts",
    "Fetch filings",
    "Module A",
    "Module B",
    "Module C",
    "Score",
)

SCORED_IDS = ("A1_DSO", "A2_DIO", "A3_ACCRUALS", "A4_CAPRATE", "A5_RESTRUCT")


class AnalysisError(Exception):
    """Base class for user-facing analysis stops (§2.3)."""


class UnsupportedFiler(AnalysisError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class NoXBRL(AnalysisError):
    def __init__(self) -> None:
        super().__init__("No structured XBRL data for this filer")


T = TypeVar("T")
Progress = Callable[[str], None]


def _noop(_stage: str) -> None:
    return None


def _run_guarded(name: str, fn: Callable[[], ModuleResult]) -> ModuleResult:
    """Run one module with a timeout; exceptions become status 'error'."""
    timeout = t("MODULE_TIMEOUT_S")
    pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"veritas-{name}")
    try:
        future = pool.submit(fn)
        return future.result(timeout=timeout)
    except cf.TimeoutError:
        log.warning("Module %s timed out after %ss", name, timeout)
        return ModuleResult("timeout", (), (), f"timed out after {int(timeout)} s", {})
    except Exception as exc:
        log.exception("Module %s failed", name)
        return ModuleResult("error", (), (), f"{type(exc).__name__}: {exc}", {})
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _choose_source(target: str, offline: bool, refresh: bool) -> DataSource:
    """Seed tickers always load the seed (no network) unless a live refresh is asked for."""
    hint = normalize_ticker(target) if not target.strip().upper().startswith("CIK") else None
    if hint in SEED_TICKERS and not refresh:
        return SeedSource()
    return get_source(offline=offline, ticker_hint=target)


# ---------------------------------------------------------------------------
# Narrative helpers
# ---------------------------------------------------------------------------
def _locate(ds: DataSource, ref: DocRef, text: str) -> Section:
    if ref.form.startswith("8-K"):
        return locate_earnings_release(text)
    if ref.form.startswith("10-K") and needs_ex13(text):
        index = ds.filing_index(ref.cik, ref.accession)
        name = find_ex13(index)
        if name:
            ex13_ref = exhibit_docref(ref, ref.form, name)
            ex13_html = ds.document(ex13_ref.url)
            if ex13_html:
                return locate_mda(text, ref.form, ex13_text=html_to_text(ex13_html))
    return locate_mda(text, ref.form)


def _narrative_docs(sel: FilingSelection) -> list[DocRef]:
    """Latest 10-K and latest 10-Q, plus the earnings 8-K that precedes each."""
    docs: list[DocRef] = []
    periodic: list[DocRef] = []
    if sel.latest_10k is not None:
        periodic.append(sel.latest_10k)
    latest_q = sel.q_pair[0] if sel.q_pair else (sel.ten_qs[0] if sel.ten_qs else None)
    if latest_q is not None:
        periodic.append(latest_q)
    for ref in periodic:
        docs.append(ref)
        ex = sel.earnings_8k.get(ref.accession)
        if ex is not None:
            # An 8-K pairs with the period of the periodic filing it precedes (§7.2.5).
            docs.append(replace(ex, report_date=ref.report_date))
    return docs


def _module_a(
    ds: DataSource,
    sel: FilingSelection,
    nf: NormalizedFacts,
    gating: Gating,
    acc: AccountingOutput,
    company: CompanyRef,
) -> ModuleResult:
    sections: list[dict[str, Any]] = []
    claims: list[Claim] = []
    if gating.kind not in ("shell", "unsupported"):
        for ref in _narrative_docs(sel):
            html = ds.document(ref.url)
            if not html:
                sections.append(_section_row(ref, None, "document unavailable"))
                continue
            section = _locate(ds, ref, html_to_text(html))
            sections.append(_section_row(ref, section, section.reason))
            if section.status == "OK":
                claims.extend(extract_claims(section, ref))

    cache: dict[tuple[str, date], CheckResult] = {}

    def checks_by_period(check_id: str, period: date) -> CheckResult | None:
        key = (check_id, period)
        if key not in cache:
            try:
                cache[key] = evaluate_check(
                    check_id, nf, period, gating, company.cik, company.ticker
                )
            except KeyError:
                return None
        return cache[key]

    latest = [c for c in acc.checks if c.check_id in SCORED_IDS]
    findings = pair_findings(checks_by_period, claims, latest)
    narrative_ok = any(s["status"] == "OK" for s in sections)
    status: ModStatus = "ok" if narrative_ok or not sections else "partial"
    reason = None if status == "ok" else "Narrative unavailable for these filings"
    extra = {
        "sections": sections,
        "claims_found": len(claims),
        "acquisition_guard": acc.acquisition_guard,
        "latest_period": acc.latest_period.isoformat() if acc.latest_period else None,
    }
    return ModuleResult(status, findings, acc.checks, reason, extra)


def _section_row(ref: DocRef, section: Section | None, reason: str | None) -> dict[str, Any]:
    return {
        "form": ref.form,
        "accession": ref.accession,
        "url": ref.url,
        "report_date": ref.report_date.isoformat() if ref.report_date else None,
        "label": section.label if section else None,
        "status": section.status if section else "NOT_FOUND",
        "word_count": section.word_count if section else 0,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Module B / C helpers
# ---------------------------------------------------------------------------
def _pair(ds: DataSource, prior: DocRef | None, current: DocRef | None) -> Any:
    if prior is None or current is None:
        return None
    prior_html = ds.document(prior.url)
    current_html = ds.document(current.url)
    if not prior_html or not current_html:
        return None
    return (prior, prior_html, current, current_html)


def _module_b(ds: DataSource, sel: FilingSelection) -> ModuleResult:
    pair_10k = _pair(ds, sel.prior_10k, sel.latest_10k)
    pair_10q = _pair(ds, sel.q_pair[1], sel.q_pair[0]) if sel.q_pair else None
    return run_footnotes(pair_10k, pair_10q)


def _ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den <= 0:
        return None
    return num / den


def _module_c(
    ds: DataSource, sel: FilingSelection, nf: NormalizedFacts, acc: AccountingOutput
) -> ModuleResult:
    proxy = None
    if sel.proxy is not None:
        html = ds.document(sel.proxy.url)
        if html:
            proxy = (sel.proxy, html)
    item11 = None
    if proxy is None and sel.latest_10k is not None:
        html = ds.document(sel.latest_10k.url)
        if html:
            item11 = (sel.latest_10k, html)

    p = acc.latest_period
    sbc = rev = fcf = gw_assets = gw_assets_prior = None
    if p is not None:
        sbc_pt, rev_pt = nf.ttm("SBC", p), nf.ttm("Revenue", p)
        ocf_pt, capex_pt = nf.ttm("OCF", p), nf.ttm("CapEx", p)
        sbc = sbc_pt.value if sbc_pt else None
        rev = rev_pt.value if rev_pt else None
        if ocf_pt is not None and capex_pt is not None:
            fcf = ocf_pt.value - capex_pt.value
        gw, assets = nf.instant("Goodwill", p), nf.instant("Assets", p)
        gw_assets = _ratio(gw.value if gw else None, assets.value if assets else None)
        prior = nf.prior_year_end(p, nf.instant_ends("Assets"))
        if prior is not None:
            gw_p, assets_p = nf.instant("Goodwill", prior), nf.instant("Assets", prior)
            gw_assets_prior = _ratio(
                gw_p.value if gw_p else None, assets_p.value if assets_p else None
            )
    return run_proxy(
        proxy, item11, sbc, rev, fcf, gw_assets, gw_assets_prior, acc.acquisition_guard
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def analyze(
    target: str,
    *,
    offline: bool = False,
    refresh: bool = False,
    progress: Progress | None = None,
) -> AnalysisResult:
    """Analyze one issuer by ticker, or by "CIK <digits>".

    Raises TickerNotFound, UnsupportedFiler or NoXBRL for the §2.3 stop cases.
    """
    say = progress or _noop
    offline = offline or get_settings().offline

    say(STAGES[0])
    ds = _choose_source(target, offline, refresh)
    company = resolve(target, ds)

    say(STAGES[1])
    submissions = ds.submissions(company.cik)
    sel = select_filings(submissions, company.cik, company.ticker, ds)
    if sel.unsupported_reason or sel.latest_10k is None:
        raise UnsupportedFiler(sel.unsupported_reason or "Unsupported filer type in v1")

    say(STAGES[2])
    facts = ds.companyfacts(company.cik)
    if facts is None:
        raise NoXBRL()
    nf = normalize(facts)
    period = latest_period(nf)
    ttm_rev_pt = nf.ttm("Revenue", period) if period else None
    gating = gate(sel.sic, True, ttm_rev_pt.value if ttm_rev_pt else None)
    if gating.kind == "unsupported":
        raise UnsupportedFiler(gating.reason)

    say(STAGES[3])
    acc = run_accounting(nf, gating, company.cik, company.ticker)

    say(STAGES[4])
    mod_a = _run_guarded("A", lambda: _module_a(ds, sel, nf, gating, acc, company))
    say(STAGES[5])
    mod_b = _run_guarded("B", lambda: _module_b(ds, sel))
    say(STAGES[6])
    mod_c = _run_guarded("C", lambda: _module_c(ds, sel, nf, acc))

    say(STAGES[7])
    breakdown = score(mod_a, mod_b, mod_c, gating)

    banners = list(dict.fromkeys(ds.banners))
    if sel.amendment_notice:
        banners.append(sel.amendment_notice)
    if gating.kind == "shell":
        banners.append("Shell / pre-revenue: forensic checks not applicable")
    if gating.scope_note:
        banners.append(gating.scope_note)
    doc_status: dict[str, str] = getattr(ds, "doc_status", {}) or {}
    for url, st in sorted(doc_status.items()):
        if st == "TRUNCATED":
            banners.append(f"TRUNCATED: a filing document exceeded the size limit ({url})")

    filings = [
        r.cite(r.form, PARSER_VERSION)
        for r in (sel.latest_10k, sel.prior_10k, *(sel.q_pair or ()), sel.proxy)
        if r is not None
    ]
    as_of = ds.fetched_at or datetime.now().replace(microsecond=0)
    return AnalysisResult(
        ticker=company.ticker,
        cik=company.cik,
        company_name=company.title,
        sic=sel.sic,
        fiscal_year_end=sel.fiscal_year_end,
        as_of=as_of,
        source=ds.source,
        parser_version=PARSER_VERSION,
        filings=tuple(filings),
        metrics=nf.metrics(),
        modules={"A": mod_a, "B": mod_b, "C": mod_c},
        score=breakdown,
        banners=tuple(banners),
    )
