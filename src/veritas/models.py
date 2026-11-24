"""Frozen data contracts shared by every layer (PRD §5.2).

The UI and PDF builder consume only `AnalysisResult`. Engine modules build the
smaller records. Everything is JSON-serializable via `to_dict()` / `to_json()`
with deterministic key ordering (PRD §0.2.3).
"""

from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

Severity = Literal["CRITICAL", "WARNING", "NO_FLAG"]
CheckState = Literal["COMPUTED", "NOT_APPLICABLE", "NOT_COMPUTABLE"]
ModStatus = Literal["ok", "partial", "unavailable", "low_confidence", "timeout", "error"]
Frequency = Literal["instant", "quarter", "fy", "ttm"]
ModuleKey = Literal["A", "B", "C"]
ClaimCluster = Literal["DEMAND", "MARGIN", "ONETIME"]
Source = Literal["live", "cache", "seed"]

SEVERITY_RANK: dict[str, int] = {"NO_FLAG": 0, "WARNING": 1, "CRITICAL": 2}


def _encode(value: Any) -> Any:
    """Convert a value into a JSON-compatible structure, deterministically."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    return value


class _Serializable:
    def to_dict(self) -> dict[str, Any]:
        encoded = _encode(self)
        assert isinstance(encoded, dict)
        return encoded

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class Citation(_Serializable):
    cik: int
    ticker: str
    form: str
    accession: str  # WITH dashes, e.g. 0000320193-24-000123
    filing_date: date
    report_date: date | None
    doc_name: str
    url: str  # full Archives URL
    section: str  # "10-K Item 7", "Note: <concept>", "DEF 14A CD&A", "XBRL"
    quote_raw: str | None  # verbatim, entity-decoded, whitespace-collapsed only
    char_start: int | None  # offsets into cached section text
    char_end: int | None
    xbrl_tag: str | None
    parser_version: str


@dataclass(frozen=True)
class DocRef(_Serializable):
    """Metadata for one fetched filing document. Not in the PRD table: it is the
    hand-off from the EDGAR layer to pure engine code (DECISIONS D-003)."""

    cik: int
    ticker: str
    form: str
    accession: str  # WITH dashes
    filing_date: date
    report_date: date | None
    doc_name: str
    url: str

    def cite(
        self,
        section: str,
        parser_version: str,
        quote_raw: str | None = None,
        char_start: int | None = None,
        char_end: int | None = None,
        xbrl_tag: str | None = None,
    ) -> Citation:
        return Citation(
            cik=self.cik,
            ticker=self.ticker,
            form=self.form,
            accession=self.accession,
            filing_date=self.filing_date,
            report_date=self.report_date,
            doc_name=self.doc_name,
            url=self.url,
            section=section,
            quote_raw=quote_raw,
            char_start=char_start,
            char_end=char_end,
            xbrl_tag=xbrl_tag,
            parser_version=parser_version,
        )


@dataclass(frozen=True)
class MetricPoint(_Serializable):
    start: date | None  # None for instants
    end: date
    value: float
    unit: str
    xbrl_tag: str
    accession: str
    form: str
    filed: date
    derived: bool  # True if computed by YTD differencing
    restated: bool  # earliest-filed value differs >1% from latest


@dataclass(frozen=True)
class MetricSeries(_Serializable):
    name: str
    frequency: Frequency
    points: tuple[MetricPoint, ...]
    tag_changed: bool


@dataclass(frozen=True)
class CheckResult(_Serializable):
    check_id: str  # "A1_DSO" ... "A5_RESTRUCT"; display-only "X_*"
    title: str
    state: CheckState
    severity: Severity | None  # None unless COMPUTED
    period_end: date | None
    inputs: dict[str, float | None]
    outputs: dict[str, float | None]
    formula: str
    reason: str | None
    annotations: tuple[str, ...]
    citations: tuple[Citation, ...]
    scored: bool
    # Not in the PRD contract table; holds per-quarter history for the Ratios
    # chart (DECISIONS D-004). Each entry: {"period_end": iso, "value": float|None}.
    history: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Claim(_Serializable):
    cluster: ClaimCluster
    citation: Citation
    period_end: date


@dataclass(frozen=True)
class Finding(_Serializable):
    id: str
    module: ModuleKey
    category: str  # scoring category, §7.5
    severity: Severity
    title: str
    explanation: str
    check: CheckResult | None
    claims: tuple[Claim, ...]
    contradiction: bool
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class ModuleResult(_Serializable):
    status: ModStatus
    findings: tuple[Finding, ...]
    checks: tuple[CheckResult, ...]
    reason: str | None
    extra: dict[str, Any]  # module-specific display payload (redlines, proxy table)


@dataclass(frozen=True)
class ScoreBreakdown(_Serializable):
    score: int | None
    coverage_num: int
    coverage_den: int
    deductions: tuple[tuple[str, int], ...]  # (category, capped points)
    label: str  # "Score 72 · coverage 4/5" or "INSUFFICIENT DATA"
    scope_note: str | None
    modules_assessed: int = 0  # "{k} of 3 modules assessed" (§7.5)
    unassessed: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnalysisResult(_Serializable):
    ticker: str
    cik: int
    company_name: str
    sic: str | None
    fiscal_year_end: str | None
    as_of: datetime
    source: Source
    parser_version: str
    filings: tuple[Citation, ...]
    metrics: dict[str, MetricSeries]
    modules: dict[ModuleKey, ModuleResult]
    score: ScoreBreakdown
    banners: tuple[str, ...]
