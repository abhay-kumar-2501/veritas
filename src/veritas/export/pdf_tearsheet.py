"""2-page PDF tear-sheet (PRD §9), reportlab Platypus flowables on a fixed 2-page canvas.

Every page is assembled from flowables, measured with ``wrap()`` and trimmed through
a ladder of detail levels until it fits its frame. As a last resort the page content
is shrunk with ``KeepInFrame``. The document therefore can never exceed two pages.
"""

from __future__ import annotations

import difflib
import io
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    KeepInFrame,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from veritas.config import DISCLAIMER, SCORE_NAME, ti
from veritas.models import (
    SEVERITY_RANK,
    AnalysisResult,
    CheckResult,
    Citation,
    Finding,
    ModuleResult,
)

# ---------------------------------------------------------------------------
# Palette (mirrors the app; NO FLAG uses a print-legible green)
# ---------------------------------------------------------------------------
INK = colors.HexColor("#0B0E14")
SLATE = colors.HexColor("#3A4252")
MUTED = colors.HexColor("#6B7280")
RULE = colors.HexColor("#C9CED6")
HAIR = colors.HexColor("#E5E8EC")
WASH = colors.HexColor("#F4F5F7")
SEV_COLOR = {
    "CRITICAL": colors.HexColor("#FF3B30"),
    "WARNING": colors.HexColor("#FFB300"),
    "NO FLAG": colors.HexColor("#00A65A"),
    "N/A": colors.HexColor("#6B7280"),
}
SEV_TEXT = {
    "CRITICAL": colors.white,
    "WARNING": INK,
    "NO FLAG": colors.white,
    "N/A": colors.white,
}
INS_COLOR = "#00804A"
DEL_COLOR = "#C4251B"

PAGE_W, PAGE_H = letter
MARGIN = 0.6 * inch
CONTENT_W = PAGE_W - 2 * MARGIN
FOOTER_H = 0.62 * inch  # reserved band above the bottom margin for the footer
FRAME_BOTTOM = MARGIN + FOOTER_H
FRAME_H = PAGE_H - MARGIN - FRAME_BOTTOM

SEV_LABEL = {"CRITICAL": "CRITICAL", "WARNING": "WARNING", "NO_FLAG": "NO FLAG"}

LIMITATIONS: tuple[str, ...] = (
    "Heuristic text matching: unusually phrased claims can be missed or mis-paired.",
    "Not modelled: FX effects, LIFO, receivables factoring/securitization, segment mix, "
    "dimensional XBRL data, company-specific extension tags.",
    "53-week fiscal years: DSO/DIO adjust via days(q); growth-based metrics do not.",
    "Restatements use as-restated values; original values are flagged, not used.",
    "Proxy metrics that appear only in images or charts are not detected.",
    "Financial issuers, foreign private issuers, funds, and shells are limited or out of scope.",
)

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
_GLYPH_MAP = {
    "−": "-",
    "‑": "-",
    "‐": "-",
    "≥": ">=",
    "≤": "<=",
    "→": "->",
    " ": " ",
    " ": " ",
    " ": " ",
    "≈": "~",
    "Δ": "d",
    "δ": "d",
    "Σ": "sum",
    "σ": "sd",
    "≠": "!=",
    "≪": "<<",
    "≫": ">>",
    "∑": "sum",
    "√": "sqrt",
    "∞": "inf",
    "⁻": "-",
    "¹": "1",
    "₁": "1",
    "₀": "0",
    "‒": "-",
    "―": "-",
    "✓": "ok",
}
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _encodable(ch: str) -> bool:
    try:
        ch.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def _fallback_char(ch: str) -> str:
    """Best-effort ASCII for a glyph the built-in fonts cannot draw (accents folded)."""
    folded = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii")
    return folded or "?"


def _safe(text: object) -> str:
    """Make text encodable by the built-in WinAnsi fonts and strip control chars."""
    s = "" if text is None else str(text)
    for k, v in _GLYPH_MAP.items():
        s = s.replace(k, v)
    s = _CTRL_RE.sub(" ", s)
    s = "".join(ch if ord(ch) < 128 or _encodable(ch) else _fallback_char(ch) for ch in s)
    return s.encode("cp1252", errors="replace").decode("cp1252")


def _esc(text: object) -> str:
    """Safe + escaped for reportlab Paragraph markup."""
    return escape(_safe(text))


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: max(0, limit - 1)]
    sp = cut.rfind(" ")
    if sp >= int(limit * 0.5):
        cut = cut[:sp]
    return cut.rstrip(" ,;:-") + "…"


def _num(value: float | None) -> str:
    if value is None:
        return "n/a"
    a = abs(value)
    if a >= 1000:
        return f"{value:,.0f}"
    if a >= 100:
        return f"{value:.1f}"
    if a >= 1:
        return f"{value:.2f}"
    if a == 0:
        return "0"
    return f"{value:.3f}"


def _iso(d: date | datetime | None) -> str:
    return d.strftime("%Y-%m-%d") if d else "n/a"


def _cite_line(c: Citation) -> str:
    return f"{c.form} · {c.accession} · {c.section}"


def pdf_filename(result: AnalysisResult) -> str:
    """``VERITAS_{TICKER}_{YYYYMMDD}.pdf`` with the date taken from ``result.as_of``."""
    ticker = re.sub(r"[^A-Za-z0-9.\-]", "", result.ticker.upper()) or "UNKNOWN"
    return f"VERITAS_{ticker}_{result.as_of.strftime('%Y%m%d')}.pdf"


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
def _style(name: str, **kw: Any) -> ParagraphStyle:
    base: dict[str, Any] = {
        "fontName": "Helvetica",
        "fontSize": 8,
        "leading": 10,
        "textColor": INK,
        "alignment": TA_LEFT,
    }
    base.update(kw)
    return ParagraphStyle(name, **base)


S_BODY = _style("body")
S_SMALL = _style("small", fontSize=7, leading=8.8, textColor=SLATE)
S_MUTED = _style("muted", fontSize=7.2, leading=9, textColor=MUTED)
S_SECTION = _style(
    "section", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=INK, spaceBefore=0
)
S_TITLE = _style("ftitle", fontName="Helvetica-Bold", fontSize=9, leading=11)
S_QUOTE = _style(
    "quote", fontName="Helvetica-Oblique", fontSize=7.8, leading=9.6, textColor=SLATE, leftIndent=0
)
S_MONO = _style("mono", fontName="Courier", fontSize=7, leading=8.6, textColor=SLATE)
S_MONO_R = _style("monor", fontName="Courier", fontSize=7.4, leading=9, alignment=TA_RIGHT)
S_CELL = _style("cell", fontSize=7.4, leading=9)
S_CELL_B = _style("cellb", fontName="Helvetica-Bold", fontSize=7.4, leading=9)
S_HEAD = _style(
    "head", fontName="Helvetica-Bold", fontSize=6.6, leading=8, textColor=MUTED, wordSpace=0
)
S_HEAD_R = _style(
    "headr", fontName="Helvetica-Bold", fontSize=6.6, leading=8, textColor=MUTED, alignment=TA_RIGHT
)
S_HEAD_C = _style(
    "headc", fontName="Helvetica-Bold", fontSize=6.6, leading=8, textColor=MUTED, alignment=1
)
S_FOOT = _style("foot", fontName="Helvetica-Oblique", fontSize=6.2, leading=7.6, textColor=MUTED)


def _p(text: str, style: ParagraphStyle = S_BODY) -> Paragraph:
    """Paragraph from already-escaped markup."""
    return Paragraph(text, style)


def _section(title: str, note: str | None = None) -> list[Flowable]:
    label = _esc(title.upper())
    if note:
        label += f' <font name="Helvetica" color="#6B7280">&nbsp;&nbsp;{_esc(note)}</font>'
    return [
        Spacer(1, 11),
        _p(f'<font color="#6B7280">■</font>&nbsp;{label}', S_SECTION),
        Spacer(1, 2),
        HRFlowable(width="100%", thickness=0.6, color=INK, spaceBefore=0, spaceAfter=3),
    ]


def _pill(sev: str, width: float = 46) -> Table:
    label = sev if sev in SEV_COLOR else "N/A"
    st = _style(
        f"pill_{label}",
        fontName="Helvetica-Bold",
        fontSize=6.4,
        leading=7.6,
        textColor=SEV_TEXT[label],
        alignment=1,
    )
    t = Table([[Paragraph(_esc(label), st)]], colWidths=[width], rowHeights=[11])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SEV_COLOR[label]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    return t


def _sev_label(sev: str | None) -> str:
    if sev is None:
        return "N/A"
    return SEV_LABEL.get(sev, "N/A")


# ---------------------------------------------------------------------------
# Data selection
# ---------------------------------------------------------------------------
_CATEGORY_LABEL = {
    "accounting": "accounting",
    "accounting_prior_period": "prior period",
    "footnotes": "footnotes",
    "footnotes_10q": "10-Q footnotes",
    "hedging": "hedging",
    "incentives": "incentives",
    "claims_consistent": "claims consistent",
}
_SCORED_CATEGORIES = ("accounting", "footnotes", "hedging", "incentives")


def _check_prefix(f: Finding) -> str:
    return f.check.check_id[:2] if f.check else ""


def _points(f: Finding, result: AnalysisResult | None = None) -> int:
    """Points this finding contributes to the score (PRD 7.5); 0 for unscored categories."""
    if f.category not in _SCORED_CATEGORIES or f.severity not in ("CRITICAL", "WARNING"):
        return 0
    if f.category == "accounting":
        pts = ti("PTS_ACCOUNTING_CRIT") if f.severity == "CRITICAL" else ti("PTS_ACCOUNTING_WARN")
        if _check_prefix(f) == "A3" and result is not None and f.check is not None:
            mod = _mod(result, "A")
            for g in mod.findings if mod else ():
                if (
                    g is not f
                    and g.category == "accounting"
                    and g.severity in ("CRITICAL", "WARNING")
                    and _check_prefix(g) in ("A1", "A2")
                    and g.check is not None
                    and g.check.period_end == f.check.period_end
                ):
                    return pts // 2
        return pts
    if f.category == "footnotes":
        return ti("PTS_FOOTNOTE")
    if f.category == "hedging":
        return ti("PTS_HEDGING")
    return ti("PTS_INCENTIVE")


def _ranked_findings(result: AnalysisResult) -> list[Finding]:
    items: list[Finding] = []
    for key in sorted(result.modules):
        mod = result.modules.get(key)
        if mod is None:
            continue
        for f in mod.findings:
            if f.severity in ("CRITICAL", "WARNING"):
                items.append(f)
    items.sort(
        key=lambda f: (
            f.category not in _SCORED_CATEGORIES,
            -SEVERITY_RANK.get(f.severity, 0),
            -_points(f, result),
        )
    )
    return items


def _dedupe_filings(result: AnalysisResult) -> list[Citation]:
    seen: set[str] = set()
    out: list[Citation] = []
    for c in result.filings:
        if c.accession in seen:
            continue
        seen.add(c.accession)
        out.append(c)
    return out


def _mod(result: AnalysisResult, key: str) -> ModuleResult | None:
    return result.modules.get(key)  # type: ignore[call-overload,no-any-return]


def _not_assessed(reason: str | None, default: str) -> str:
    return f"Not assessed: {_clip(reason or default, 160)}"


# ---------------------------------------------------------------------------
# Page 1
# ---------------------------------------------------------------------------
def _header(result: AnalysisResult) -> Table:
    left = [
        _p(
            '<font name="Helvetica-Bold" size="7" color="#9AA3B2">VERITAS</font>'
            '<font name="Helvetica" size="7" color="#9AA3B2">&nbsp;&nbsp;|&nbsp;&nbsp;'
            "NARRATIVE VS NUMBERS SCREENING TEAR-SHEET</font>",
            _style("h0", leading=9),
        ),
        Spacer(1, 3),
        _p(
            f'<font name="Helvetica-Bold" size="17" color="white">'
            f"{_esc(result.company_name)}</font>",
            _style("h1", fontSize=17, leading=20),
        ),
    ]
    meta = (
        f"TICKER {_esc(result.ticker)}&nbsp;&nbsp;·&nbsp;&nbsp;CIK {result.cik}"
        f"&nbsp;&nbsp;·&nbsp;&nbsp;SIC {_esc(result.sic or 'n/a')}"
    )
    right = [
        _p(
            f'<font name="Courier" size="7.4" color="#D5DAE3">{meta}</font>',
            _style("h2", alignment=TA_RIGHT, leading=9.5),
        ),
        _p(
            f'<font name="Courier" size="7.4" color="#D5DAE3">DATA AS OF {_iso(result.as_of)}'
            f"&nbsp;&nbsp;·&nbsp;&nbsp;SOURCE {_esc(result.source.upper())}</font>",
            _style("h3", alignment=TA_RIGHT, leading=9.5),
        ),
        _p(
            f'<font name="Courier" size="7.4" color="#D5DAE3">PARSER {_esc(result.parser_version)}'
            f"&nbsp;&nbsp;·&nbsp;&nbsp;FYE {_esc(result.fiscal_year_end or 'n/a')}</font>",
            _style("h4", alignment=TA_RIGHT, leading=9.5),
        ),
    ]
    t = Table([[left, right]], colWidths=[CONTENT_W * 0.55, CONTENT_W * 0.45])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), INK),
                ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
                ("VALIGN", (1, 0), (1, 0), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return t


def _filings_block(result: AnalysisResult, max_rows: int) -> list[Flowable]:
    out = _section("Filings used")
    filings = _dedupe_filings(result)
    if not filings:
        out.append(_p(_esc("Not assessed: no filings recorded for this result."), S_MUTED))
        return out
    shown = filings[:max_rows]
    rows: list[list[Any]] = [
        [_p("FORM", S_HEAD), _p("FILED", S_HEAD), _p("ACCESSION", S_HEAD), _p("DOCUMENT", S_HEAD)]
    ]
    for c in shown:
        rows.append(
            [
                _p(_esc(c.form), S_CELL_B),
                _p(_iso(c.filing_date), S_MONO),
                _p(_esc(c.accession), S_MONO),
                _p(_esc(_clip(c.doc_name, 48)), S_CELL),
            ]
        )
    t = Table(rows, colWidths=[52, 66, 128, CONTENT_W - 246], hAlign="LEFT")
    t.setStyle(_grid_style(len(rows)))
    out.append(t)
    extra = len(filings) - len(shown)
    if extra > 0:
        out.append(_p(f"+{extra} more in app", S_MUTED))
    return out


def _grid_style(n_rows: int, header: bool = True) -> TableStyle:
    cmds: list[tuple[Any, ...]] = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, HAIR),
    ]
    if header:
        cmds.append(("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE))
    return TableStyle(cmds)


def _score_box(result: AnalysisResult) -> Table:
    sc = result.score
    score_txt = str(sc.score) if sc.score is not None else "INSUFFICIENT DATA"
    big = sc.score is not None
    left: list[Flowable] = [
        _p(
            f'<font name="Helvetica-Bold" size="6.4" color="#9AA3B2">'
            f"{_esc(SCORE_NAME.upper())}</font>",
            _style("sb0", leading=8),
        ),
        Spacer(1, 3),
        _p(
            f'<font name="Helvetica-Bold" size="{34 if big else 14}" color="white">'
            f"{_esc(score_txt)}</font>"
            + ('<font name="Helvetica" size="10" color="#9AA3B2"> / 100</font>' if big else ""),
            _style("sb1", fontSize=34, leading=38 if big else 20),
        ),
        Spacer(1, 3),
        _p(
            f'<font name="Courier" size="7.2" color="#D5DAE3">'
            f"COVERAGE {sc.coverage_num}/{sc.coverage_den}</font>",
            _style("sb2", leading=9),
        ),
        _p(
            f'<font name="Courier" size="7.2" color="#D5DAE3">'
            f"{sc.modules_assessed} OF 3 MODULES ASSESSED</font>",
            _style("sb3", leading=9),
        ),
    ]
    right: list[Flowable] = [
        _p(
            f'<font color="#6B7280">{_esc(sc.label)}</font>',
            _style("sr0", fontName="Helvetica-Bold", fontSize=7, leading=9),
        )
    ]
    ded_rows: list[list[Any]] = [[_p("DEDUCTION CATEGORY", S_HEAD), _p("POINTS", S_HEAD_R)]]
    total = 0
    for cat, pts in sc.deductions:
        total += pts
        ded_rows.append([_p(_esc(cat.capitalize()), S_CELL), _p(f"-{pts}", S_MONO_R)])
    if not sc.deductions:
        ded_rows.append([_p("No deductions", S_CELL), _p("0", S_MONO_R)])
    else:
        ded_rows.append([_p("Total (capped)", S_CELL_B), _p(f"-{total}", S_MONO_R)])
    dt = Table(ded_rows, colWidths=[150, 50], hAlign="LEFT")
    dt.setStyle(_grid_style(len(ded_rows)))
    right.append(Spacer(1, 2))
    right.append(dt)
    notes: list[str] = []
    if sc.scope_note:
        notes.append(f"Scope: {sc.scope_note}")
    if sc.unassessed:
        notes.append("Unassessed (no deduction): " + ", ".join(sc.unassessed))
    for n in notes:
        right.append(Spacer(1, 2))
        right.append(_p(_esc(_clip(n, 200)), S_MUTED))
    t = Table([[left, right]], colWidths=[165, CONTENT_W - 165])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), INK),
                ("BOX", (0, 0), (-1, -1), 0.8, INK),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 10),
                ("RIGHTPADDING", (0, 0), (0, 0), 8),
                ("LEFTPADDING", (1, 0), (1, 0), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return t


_OUT_LABEL = {
    "dso": "DSO",
    "dio": "DIO",
    "dpo": "DPO",
    "dso_prior_year": "DSO prior yr",
    "dio_prior_year": "DIO prior yr",
    "delta_dso_days": "dDSO days",
    "delta_dio_days": "dDIO days",
    "dso_rel_change": "DSO chg",
    "dio_rel_change": "DIO chg",
    "accrual_ratio": "Accrual ratio",
    "ccr": "Cash conversion",
    "ocf_ttm_growth": "OCF growth",
    "material_quarters_last4": "Material qtrs (L4)",
    "material_quarters_last8": "Material qtrs (L8)",
    "quarters_with_data_last4": "Qtrs w/ data (L4)",
    "quarters_with_data_last8": "Qtrs w/ data (L8)",
}


def _out_label(key: str) -> str:
    return _OUT_LABEL.get(key.lower(), key.replace("_", " ").strip().capitalize())


def _fmt_out(key: str, v: float | None) -> str:
    if v is None:
        return "n/a"
    k = key.lower()
    if "quarters" in k or "count" in k or k.endswith("_points"):
        return str(round(v))
    if "rel_change" in k or "ratio" in k or "growth" in k:
        return f"{v * 100:.1f}%"
    if "ccr" in k:
        return f"{v:.2f}x"
    if "days" in k or k in ("dso", "dio", "dpo") or k.endswith("_prior_year"):
        return f"{v:.1f}"
    return _num(v)


def _formula_head(formula: str, limit: int = 110) -> str:
    """First sentence/clause of a formula, only if it fits whole."""
    formula = re.sub(r"\s*\((?:WARNING|CRITICAL)[^)]*\)", "", formula)
    head = re.split(r";\s|\.\s", formula.strip(), maxsplit=1)[0].rstrip(".")
    return head if 0 < len(head) <= limit else ""


def _metric_line(f: Finding, max_len: int = 210) -> str:
    chk = f.check
    if chk is None:
        return ""
    parts: list[str] = []
    fh = _formula_head(chk.formula)
    if fh:
        parts.append(fh)
    outs: list[str] = []
    total = sum(len(p) + 3 for p in parts)
    for k, v in chk.outputs.items():
        if v is None:
            continue
        item = f"{_out_label(k)} {_fmt_out(k, v)}"
        if total + len(item) + 2 > max_len or len(outs) >= 4:
            break
        outs.append(item)
        total += len(item) + 2
    if outs:
        parts.append(", ".join(outs))
    return " | ".join(parts)


def _finding_quote(f: Finding) -> str | None:
    for cl in f.claims:
        q = cl.citation.quote_raw
        if q:
            return q
    return None


def _finding_cites(f: Finding) -> list[Citation]:
    pool: list[Citation] = list(f.citations)
    for cl in f.claims:
        pool.append(cl.citation)
    if f.check:
        pool.extend(f.check.citations)
    seen: set[tuple[str, str]] = set()
    out: list[Citation] = []
    for c in pool:
        key = (c.accession, c.section)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _finding_block(
    result: AnalysisResult,
    rank: int,
    f: Finding,
    detail: int,
    quote_cap: int = 240,
    max_cites: int = 2,
) -> Flowable:
    pill = _pill(_sev_label(f.severity))
    pts = _points(f, result)
    label = _CATEGORY_LABEL.get(f.category, f.category.replace("_", " "))
    cat = f"MOD {f.module} · {label}".upper()
    pts_txt = f"-{pts} pts" if pts else "unscored"
    head = Table(
        [
            [
                _p(f"{rank}", _style("rk", fontName="Courier-Bold", fontSize=9, leading=11)),
                pill,
                _p(_esc(_clip(f.title, 110)), S_TITLE),
                _p(
                    f'<font name="Courier" color="#6B7280">{_esc(cat)}&nbsp;&nbsp;{pts_txt}</font>',
                    _style("fh", fontSize=6.6, leading=8, alignment=TA_RIGHT),
                ),
            ]
        ],
        colWidths=[14, 50, CONTENT_W - 14 - 50 - 170 - 8, 170],
    )
    head.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    body: list[Flowable] = [head, Spacer(1, 3)]
    quote = _finding_quote(f)
    metric = _metric_line(f)
    show_expl = detail >= 2 or (detail == 1 and not quote and not metric)
    if show_expl and f.explanation:
        body.append(_p(_esc(_clip(f.explanation, 230)), S_SMALL))
        body.append(Spacer(1, 2))
    if quote and detail >= 1:
        body.append(_p(f"\u201c{_esc(_clip(quote, quote_cap))}\u201d", S_QUOTE))
    if metric and detail >= 1:
        body.append(Spacer(1, 1.5))
        body.append(_p(_esc(metric), S_MONO))
    cites = _finding_cites(f)[:max_cites]
    if cites:
        body.append(Spacer(1, 1.5))
        body.append(
            _p(
                '<font name="Helvetica-Bold" color="#6B7280">SOURCE</font>&nbsp;&nbsp;'
                + "&nbsp;&nbsp;|&nbsp;&nbsp;".join(_esc(_cite_line(c)) for c in cites),
                S_MONO,
            )
        )
    wrapper = Table([[body]], colWidths=[CONTENT_W])
    wrapper.setStyle(
        TableStyle(
            [
                ("LINEBEFORE", (0, 0), (0, -1), 2.2, SEV_COLOR[_sev_label(f.severity)]),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, HAIR),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return wrapper


def _findings_block(
    result: AnalysisResult, n_show: int, detail: int, quote_cap: int, max_cites: int
) -> list[Flowable]:
    ranked = _ranked_findings(result)
    out = _section("Top findings", "ordered by severity, then score points")
    if not ranked:
        mods = [m for m in result.modules.values() if m.status in ("ok", "partial")]
        msg = (
            "No CRITICAL or WARNING findings in assessed modules."
            if mods
            else "Not assessed: no module produced findings."
        )
        out.append(_p(_esc(msg), S_MUTED))
        return out
    shown = ranked[:n_show]
    for i, f in enumerate(shown, 1):
        out.append(_finding_block(result, i, f, detail, quote_cap, max_cites))
    more = len(ranked) - len(shown)
    if more > 0:
        out.append(Spacer(1, 3))
        out.append(
            _p(f"+{more} more in app (showing {len(shown)} of {len(ranked)} flagged)", S_MUTED)
        )
    return out


def _banner_line(result: AnalysisResult) -> list[Flowable]:
    if not result.banners:
        return []
    txt = "  ·  ".join(_clip(b, 260) for b in result.banners[:2])
    return [Spacer(1, 4), _p(f"<b>Notice:</b> {_esc(txt)}", S_MUTED)]


# (n_findings, detail, quote_cap, max_cites, filings_rows, show_banner)
_P1_LADDER: tuple[tuple[int, int, int, int, int, bool], ...] = (
    (5, 2, 240, 2, 6, True),
    (5, 1, 240, 2, 6, True),
    (5, 1, 240, 1, 4, True),
    (5, 1, 200, 1, 3, True),
    (5, 1, 140, 1, 2, False),
    (5, 0, 140, 1, 2, False),
    (4, 0, 100, 1, 1, False),
    (3, 0, 100, 1, 1, False),
    (2, 0, 100, 1, 1, False),
    (1, 0, 100, 1, 1, False),
)


def _page1(result: AnalysisResult, level: int) -> list[Flowable]:
    n, detail, qcap, cites, frows, banner = _P1_LADDER[level]
    items: list[Flowable] = [_header(result)]
    if banner:
        items += _banner_line(result)
    items += _filings_block(result, frows)
    items += _section("Score summary")
    items.append(_score_box(result))
    items += _findings_block(result, n, detail, qcap, cites)
    return items


# ---------------------------------------------------------------------------
# Page 2
# ---------------------------------------------------------------------------
_CHECK_IDS = ("A1", "A2", "A3", "A4", "A5")


def _check_by_prefix(mod: ModuleResult | None, prefix: str) -> CheckResult | None:
    if mod is None:
        return None
    for c in mod.checks:
        if c.check_id.startswith(prefix + "_") or c.check_id == prefix:
            return c
    return None


def _history_points(chk: CheckResult) -> list[tuple[str, float | None]]:
    pts: list[tuple[str, float | None]] = []
    for h in chk.history or ():
        if not isinstance(h, dict):
            continue
        pe = h.get("period_end")
        val = h.get("value")
        pts.append((str(pe) if pe else "", float(val) if isinstance(val, (int, float)) else None))
    pts.sort(key=lambda p: p[0])
    return pts[-3:]


_HEADLINE_KEY = {
    "A1": "dso",
    "A2": "dio",
    "A3": "accrual_ratio",
    "A5": "material_quarters_last4",
}


def _headline(chk: CheckResult) -> tuple[str, float | None]:
    pref = _HEADLINE_KEY.get(chk.check_id[:2])
    if pref is not None and pref in chk.outputs:
        return pref, chk.outputs[pref]
    for k, v in chk.outputs.items():
        if v is not None:
            return k, v
    for k, v in chk.outputs.items():
        return k, v
    return pref or "", None


def _cell_val(date_txt: str, value: float | None, key: str = "") -> list[Flowable]:
    return [
        _p(_fmt_out(key, value) if key else _num(value), S_MONO_R),
        _p(
            f'<font color="#6B7280">{_esc(date_txt)}</font>',
            _style("cd", fontName="Courier", fontSize=5.8, leading=7, alignment=TA_RIGHT),
        ),
    ]


def _check_table(result: AnalysisResult) -> list[Flowable]:
    out = _section("Module A: accounting checks", "latest 3 evaluation points")
    mod = _mod(result, "A")
    if mod is None or (mod.status in ("unavailable", "error", "timeout") and not mod.checks):
        out.append(
            _p(_esc(_not_assessed(mod.reason if mod else None, "Module A unavailable")), S_MUTED)
        )
        return out
    head = [
        _p("CHECK", S_HEAD),
        _p("METRIC", S_HEAD),
        _p("EVAL -2", S_HEAD_R),
        _p("EVAL -1", S_HEAD_R),
        _p("LATEST", S_HEAD_R),
        _p("STATUS", S_HEAD_C),
    ]
    rows: list[list[Any]] = [head]
    spans: list[int] = []
    for cid in _CHECK_IDS:
        chk = _check_by_prefix(mod, cid)
        if chk is None:
            rows.append(
                [
                    _p(f"<b>{cid}</b>", S_CELL),
                    _p("Not assessed: check not available", S_MUTED),
                    "",
                    "",
                    "",
                    _pill("N/A", 40),
                ]
            )
            spans.append(len(rows) - 1)
            continue
        label = f"<b>{cid}</b>&nbsp;{_esc(_clip(chk.title, 44))}"
        if chk.state != "COMPUTED":
            reason = chk.reason or (
                "not applicable" if chk.state == "NOT_APPLICABLE" else "not computable"
            )
            rows.append(
                [
                    _p(label, S_CELL),
                    _p(_esc(_not_assessed(reason, "not computable")), S_MUTED),
                    "",
                    "",
                    "",
                    _pill("N/A", 40),
                ]
            )
            spans.append(len(rows) - 1)
            continue
        key, latest = _headline(chk)
        hist = _history_points(chk)
        pe = _iso(chk.period_end) if chk.period_end else ""
        cells = list(hist) if hist else [(pe, latest)]
        while len(cells) < 3:
            cells.insert(0, ("", None))
        rows.append(
            [
                _p(label, S_CELL),
                _p(_esc(_out_label(key) if key else "n/a"), S_MONO),
                _cell_val(cells[0][0], cells[0][1], key)
                if cells[0][0] or cells[0][1] is not None
                else _p('<font color="#6B7280">&mdash;</font>', S_MONO_R),
                _cell_val(cells[1][0], cells[1][1], key)
                if cells[1][0] or cells[1][1] is not None
                else _p('<font color="#6B7280">&mdash;</font>', S_MONO_R),
                _cell_val(cells[2][0], cells[2][1], key),
                _pill(_sev_label(chk.severity), 40),
            ]
        )
    cw = [0, 96, 70, 70, 70, 46]
    cw[0] = CONTENT_W - sum(cw[1:])
    t = Table(rows, colWidths=cw, hAlign="LEFT")
    style = _grid_style(len(rows))
    for r in spans:
        style.add("SPAN", (1, r), (4, r))
    t.setStyle(style)
    out.append(t)
    return out


# ---- redline ---------------------------------------------------------------
def _collect_edits(mod: ModuleResult | None) -> list[dict[str, Any]]:
    edits: list[dict[str, Any]] = []
    if mod is None:
        return edits
    pairs = mod.extra.get("pairs") if isinstance(mod.extra, dict) else None
    if not isinstance(pairs, dict):
        return edits
    for pair_key in sorted(pairs):
        pair = pairs[pair_key]
        if not isinstance(pair, dict):
            continue
        for note in pair.get("notes") or ():
            if not isinstance(note, dict):
                continue
            for row in note.get("rows") or ():
                if isinstance(row, dict) and row.get("material"):
                    edits.append(
                        {
                            "form": str(pair_key),
                            "note": str(note.get("title") or note.get("key") or "Note"),
                            "row": row,
                        }
                    )
    edits.sort(key=lambda e: 0 if e["row"].get("softened") else 1)
    return edits


def _redline_markup(row: dict[str, Any], cap: int) -> str:
    kind = str(row.get("kind") or "").upper()
    prior = " ".join(str(row.get("prior") or "").split())
    cur = " ".join(str(row.get("current") or "").split())

    def ins(t: str) -> str:
        return f'<font color="{INS_COLOR}"><b>[+{_esc(t)}+]</b></font>'

    def dele(t: str) -> str:
        return f'<font color="{DEL_COLOR}"><b>[-{_esc(t)}-]</b></font>'

    if kind == "INSERTED" or (not prior and cur):
        return ins(_clip(cur, cap))
    if kind == "DELETED" or (prior and not cur):
        return dele(_clip(prior, cap))
    a, b = prior.split(), cur.split()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    parts: list[str] = []
    budget = cap
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            words = a[i1:i2]
            if len(words) > 14:
                words = [*words[:6], "…", *words[-6:]]
            txt = " ".join(words)
            parts.append(_esc(txt))
            budget -= len(txt)
            continue
        if i2 > i1:
            txt = " ".join(a[i1:i2])
            parts.append(dele(txt if len(txt) < 160 else _clip(txt, 160)))
            budget -= min(len(txt), 160)
        if j2 > j1:
            txt = " ".join(b[j1:j2])
            parts.append(ins(txt if len(txt) < 160 else _clip(txt, 160)))
            budget -= min(len(txt), 160)
    return " ".join(parts)


def _redline_block(result: AnalysisResult, n_edits: int, cap: int) -> list[Flowable]:
    out = _section("Module B: top material footnote edits", "[+inserted+]  [-deleted-]")
    mod = _mod(result, "B")
    if mod is None or mod.status in ("unavailable", "error", "timeout"):
        out.append(
            _p(_esc(_not_assessed(mod.reason if mod else None, "Module B unavailable")), S_MUTED)
        )
        return out
    edits = _collect_edits(mod)
    if not edits:
        out.append(_p("No material footnote edits identified in the compared filings.", S_MUTED))
    for e in edits[:n_edits]:
        row = e["row"]
        tag = f"{e['form']} · {e['note']} · {str(row.get('kind') or '').upper()}"
        if row.get("softened"):
            tag += " · SOFTENED"
        out.append(
            _p(
                f'<font name="Courier" size="6.6" color="#6B7280">{_esc(_clip(tag, 90))}</font>',
                S_SMALL,
            )
        )
        out.append(_p(_redline_markup(row, cap), _style("rl", fontSize=7.6, leading=9.6)))
        out.append(Spacer(1, 3))
    more = len(edits) - min(len(edits), n_edits)
    if more > 0:
        out.append(_p(f"+{more} more in app", S_MUTED))
    hedge = mod.extra.get("hedging") if isinstance(mod.extra, dict) else None
    if isinstance(hedge, dict) and hedge:
        status = str(hedge.get("label") or hedge.get("status") or "n/a")
        pr, cr = hedge.get("prior_rate"), hedge.get("current_rate")
        if isinstance(pr, (int, float)) and isinstance(cr, (int, float)):
            txt = (
                f"Hedging drift: {status}. Rate per 1,000 tokens {pr:.1f} to {cr:.1f}"
                f" (hits {hedge.get('prior_hits', 'n/a')} to {hedge.get('current_hits', 'n/a')})."
            )
        else:
            txt = f"Hedging drift: {status}."
        out.append(_p(_esc(txt), S_MONO))
    return out


# ---- proxy -----------------------------------------------------------------
def _proxy_block(result: AnalysisResult, n_metrics: int, n_flags: int) -> list[Flowable]:
    out = _section("Module C: proxy incentive metrics and flags")
    mod = _mod(result, "C")
    extra = mod.extra if mod is not None and isinstance(mod.extra, dict) else {}
    seen: set[tuple[str, str]] = set()
    metrics: list[dict[str, Any]] = []
    for m in extra.get("metrics") or ():
        if not isinstance(m, dict):
            continue
        key = (str(m.get("metric") or "").upper(), str(m.get("context") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        metrics.append(m)
    if mod is None or mod.status not in ("ok", "partial") or not metrics:
        reason = mod.reason if mod else None
        if mod is not None and not reason and mod.status in ("ok", "partial"):
            reason = "no incentive metrics extracted"
        out.append(_p(_esc(_not_assessed(reason, "proxy not available")), S_MUTED))
        return out
    if metrics:
        rows: list[list[Any]] = [
            [
                _p("METRIC", S_HEAD),
                _p("CONTEXT", S_HEAD),
                _p("WEIGHT", S_HEAD_R),
                _p("SOURCE", S_HEAD),
            ]
        ]
        for m in metrics[:n_metrics]:
            w = m.get("weight")
            wtxt = f"{w:g}%" if isinstance(w, (int, float)) else "weight not parsed"
            raw_cit = m.get("citation")
            cit: dict[str, Any] = raw_cit if isinstance(raw_cit, dict) else {}
            src = str(extra.get("source") or cit.get("section") or "")
            rows.append(
                [
                    _p(_esc(str(m.get("metric") or "n/a")), S_CELL_B),
                    _p(_esc(_clip(str(m.get("context") or ""), 40)), S_CELL),
                    _p(_esc(wtxt), S_MONO_R),
                    _p(_esc(_clip(src, 40)), S_MUTED),
                ]
            )
        t = Table(rows, colWidths=[110, 150, 90, CONTENT_W - 350], hAlign="LEFT")
        t.setStyle(_grid_style(len(rows)))
        out.append(t)
        if len(metrics) > n_metrics:
            out.append(_p(f"+{len(metrics) - n_metrics} more in app", S_MUTED))
    flags = [f for f in mod.findings if f.severity != "NO_FLAG"]
    out.append(Spacer(1, 3))
    if flags:
        for f in flags[:n_flags]:
            out.append(
                _p(
                    f"<b>{_esc(_sev_label(f.severity))}</b>&nbsp;&nbsp;<b>{_esc(f.title)}</b>"
                    f": {_esc(_clip(f.explanation, 170))}",
                    S_CELL,
                )
            )
        if len(flags) > n_flags:
            out.append(_p(f"+{len(flags) - n_flags} more in app", S_MUTED))
    else:
        out.append(_p("NO FLAG: no proxy flags raised.", S_MUTED))
    for n in list(extra.get("notes") or ())[:2]:
        out.append(_p(f"Note: {_esc(_clip(str(n), 160))}", S_MUTED))
    return out


def _limits_block(n_items: int) -> list[Flowable]:
    out = _section("Known limitations", "abbreviated; full text in app")
    for line in LIMITATIONS[:n_items]:
        out.append(_p(f"•&nbsp;{_esc(line)}", S_SMALL))
    if n_items < len(LIMITATIONS):
        out.append(_p(f"+{len(LIMITATIONS) - n_items} more in app", S_MUTED))
    return out


def _method_block(result: AnalysisResult, short: bool) -> list[Flowable]:
    out = _section("Methodology")
    txt = (
        f"The {SCORE_NAME} is a capped, coverage-aware 0-100 screening score. It starts at 100 and "
        "subtracts capped points per category (accounting, footnotes, incentives, hedging); "
        "unassessed modules deduct nothing and are listed as unassessed. The score is uncalibrated "
        "and not comparable across sectors. All text analysis is heuristic. "
        f"Parser version {result.parser_version}; the same inputs produce the same output."
    )
    if short:
        txt = (
            f"The {SCORE_NAME} is a capped 0-100 screening score; it is uncalibrated and not "
            "comparable across sectors. Text analysis is heuristic."
        )
    out.append(_p(_esc(txt), S_SMALL))
    mods = []
    for k in ("A", "B", "C"):
        m = _mod(result, k)
        mods.append(f"{k}: {m.status if m else 'not run'}")
    out.append(Spacer(1, 2))
    out.append(_p(_esc("Module status  " + "  |  ".join(mods)), S_MONO))
    return out


# (n_checks_rows unused, n_edits, edit_cap, n_metrics, n_flags, n_limits, short_method)
_P2_LADDER: tuple[tuple[int, int, int, int, int, bool], ...] = (
    (3, 380, 6, 2, 6, False),
    (3, 300, 5, 2, 6, False),
    (3, 240, 4, 2, 4, True),
    (2, 220, 3, 1, 3, True),
    (1, 180, 3, 1, 2, True),
    (1, 120, 2, 1, 1, True),
    (0, 100, 1, 1, 1, True),
)


def _page2(result: AnalysisResult, level: int) -> list[Flowable]:
    n_edits, cap, n_metrics, n_flags, n_limits, short = _P2_LADDER[level]
    items: list[Flowable] = []
    items.append(
        _p(
            f'<font name="Helvetica-Bold" size="11">{_esc(result.company_name)}</font>'
            f'<font name="Courier" size="8" color="#6B7280">&nbsp;&nbsp;{_esc(result.ticker)}'
            f"&nbsp;&nbsp;CIK {result.cik}&nbsp;&nbsp;AS OF {_iso(result.as_of)}</font>",
            _style("p2h", fontSize=11, leading=14),
        )
    )
    items.append(Spacer(1, 3))
    items.append(HRFlowable(width="100%", thickness=2, color=INK, spaceBefore=0, spaceAfter=0))
    items += _check_table(result)
    items += _redline_block(result, n_edits, cap)
    items += _proxy_block(result, n_metrics, n_flags)
    items += _limits_block(n_limits)
    items += _method_block(result, short)
    return items


# ---------------------------------------------------------------------------
# Layout engine
# ---------------------------------------------------------------------------
def _height(items: Iterable[Flowable]) -> float:
    total = 0.0
    for f in items:
        _w, h = f.wrap(CONTENT_W, 10_000)
        total += h
        total += (
            getattr(f, "getSpaceBefore", lambda: 0)() + getattr(f, "getSpaceAfter", lambda: 0)()
        )
    return total


def _fit(builder: Callable[[int], list[Flowable]], levels: int) -> list[Flowable]:
    """Return the richest content that fits the frame (shrink-wrapped as a last resort)."""
    last: list[Flowable] = []
    for level in range(levels):
        items = builder(level)
        last = items
        if _height(items) <= FRAME_H - 2:
            return items
    return [KeepInFrame(CONTENT_W, FRAME_H - 2, last, mode="shrink")]


def _draw_stack(c: rl_canvas.Canvas, items: Sequence[Flowable]) -> None:
    y = PAGE_H - MARGIN
    for f in items:
        sb = getattr(f, "getSpaceBefore", lambda: 0)()
        sa = getattr(f, "getSpaceAfter", lambda: 0)()
        _w, h = f.wrap(CONTENT_W, 10_000)
        y -= sb
        f.drawOn(c, MARGIN, y - h)
        y -= h + sa


def _footer(c: rl_canvas.Canvas, result: AnalysisResult, page: int) -> None:
    c.saveState()
    y_top = MARGIN + FOOTER_H - 6
    c.setStrokeColor(INK)
    c.setLineWidth(0.8)
    c.line(MARGIN, y_top, PAGE_W - MARGIN, y_top)
    para = Paragraph(f"<b>DISCLAIMER.</b> {_esc(DISCLAIMER)}", S_FOOT)
    _w, h = para.wrap(CONTENT_W, 100)
    para.drawOn(c, MARGIN, y_top - 4 - h)
    c.setFont("Courier", 6.2)
    c.setFillColor(MUTED)
    c.drawString(
        MARGIN,
        MARGIN - 2,
        _safe(f"VERITAS  |  {result.ticker}  |  parser {result.parser_version}"),
    )
    c.drawRightString(PAGE_W - MARGIN, MARGIN - 2, f"PAGE {page} OF 2")
    c.restoreState()


def build_pdf(result: AnalysisResult) -> bytes:
    """Render the 2-page tear-sheet and return the PDF bytes (always exactly 2 pages)."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(
        buf,
        pagesize=letter,
        invariant=1,
        pageCompression=1,
        title=_safe(f"VERITAS tear-sheet {result.ticker}"),
        author="VERITAS",
        subject=_safe(SCORE_NAME),
    )
    page1 = _fit(lambda lv: _page1(result, lv), len(_P1_LADDER))
    page2 = _fit(lambda lv: _page2(result, lv), len(_P2_LADDER))
    for n, items in ((1, page1), (2, page2)):
        c.setFillColor(colors.white)
        c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
        _draw_stack(c, items)
        _footer(c, result, n)
        c.showPage()
    c.save()
    return buf.getvalue()
