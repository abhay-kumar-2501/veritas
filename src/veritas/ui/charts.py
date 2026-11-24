"""Plotly figures for the Ratios view, sharing one dark template.

Threshold bands come from `config.t()` so the chart always matches the
engine's rules (PRD §0.2.1). A band is drawn only where the rule can be shown
honestly as a level on this chart; otherwise `band_note()` explains why not.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import plotly.graph_objects as go

from veritas.config import t

# Mirrors the CSS tokens in styles.css (Plotly cannot read CSS variables).
BG = "rgba(0,0,0,0)"
TEXT = "#E6EAF2"
TEXT_3 = "rgba(230,234,242,0.56)"
HAIRLINE = "rgba(255,255,255,0.05)"
ACCENT = "#5AC8FA"
WARN_FILL = "rgba(255,179,0,0.10)"
WARN_LINE = "rgba(255,179,0,0.55)"
CRIT_FILL = "rgba(255,59,48,0.09)"
CRIT_LINE = "rgba(255,59,48,0.55)"
PANEL = "#131822"
MONO = '"JetBrains Mono","SF Mono",Menlo,Consolas,monospace'
SANS = '-apple-system,BlinkMacSystemFont,"SF Pro Display","Inter",system-ui,sans-serif'

PLOT_CONFIG: dict[str, Any] = {"displayModeBar": False, "responsive": True}

# Per-check presentation: value axis format and band type.
#   rel_days: band = prior-year same quarter + max(abs, rel * prior)
#   abs_level: horizontal bands at fixed levels
#   rel_pp: band = prior-year same quarter + percentage points
_SPEC: dict[str, dict[str, Any]] = {
    "A1_DSO": {"unit": " d", "fmt": ",.1f", "band": "rel_days", "key": "DSO"},
    "A2_DIO": {"unit": " d", "fmt": ",.1f", "band": "rel_days", "key": "DSO"},
    "A3_ACCRUALS": {"unit": "", "fmt": ".1%", "band": "abs_level"},
    "A4_CAPRATE": {"unit": "", "fmt": ".1%", "band": "rel_pp"},
    "A5_RESTRUCT": {"unit": "", "fmt": ",.2f", "band": None},
}


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def history_points(history: Sequence[Any]) -> list[tuple[date, float | None]]:
    """Normalise CheckResult.history into sorted (period_end, value) pairs."""
    out: list[tuple[date, float | None]] = []
    for h in history or ():
        if not isinstance(h, Mapping):
            continue
        d = _parse_date(h.get("period_end"))
        if d is None:
            continue
        v = h.get("value")
        val = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        out.append((d, val))
    out.sort(key=lambda p: p[0])
    return out


def band_note(check_id: str) -> str:
    spec = _SPEC.get(check_id, {})
    band = spec.get("band")
    if band == "rel_days":
        return (
            "Shaded zones: the level that would meet the WARNING / CRITICAL magnitude test versus "
            "the same quarter a year earlier. CRITICAL also requires persistence (not drawn)."
        )
    if band == "abs_level":
        return (
            "Shaded zones: accrual-ratio WARNING and CRITICAL levels. The cash-conversion "
            "test is shown in the card inputs, not on this chart."
        )
    if band == "rel_pp":
        return (
            "Shaded zones: the capitalization rate that would be ≥ 5 pp / 10 pp above a year ago."
        )
    if check_id == "A5_RESTRUCT":
        return (
            "No band: this check counts quarters with a material charge rather than comparing "
            "a level."
        )
    return "No threshold band for this metric."


def _base_layout(fmt: str, unit: str) -> dict[str, Any]:
    return {
        "paper_bgcolor": BG,
        "plot_bgcolor": BG,
        "height": 280,
        "margin": {"l": 8, "r": 12, "t": 30, "b": 8},
        "font": {"family": MONO, "size": 11, "color": TEXT_3},
        "showlegend": True,
        "legend": {
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.04,
            "xanchor": "right",
            "x": 1.0,
            "font": {"family": MONO, "size": 10, "color": TEXT_3},
            "bgcolor": BG,
        },
        "hovermode": "x unified",
        "hoverlabel": {
            "bgcolor": PANEL,
            "bordercolor": ACCENT,
            "font": {"family": MONO, "size": 12, "color": TEXT},
        },
        "xaxis": {
            "showgrid": False,
            "zeroline": False,
            "showline": True,
            "linecolor": HAIRLINE,
            "ticks": "",
            "tickformat": "%b %y",
            "automargin": True,
            "tickfont": {"family": MONO, "size": 10, "color": TEXT_3},
        },
        "yaxis": {
            "showgrid": True,
            "gridcolor": HAIRLINE,
            "zeroline": False,
            "tickformat": fmt,
            "ticksuffix": unit,
            "tickfont": {"family": MONO, "size": 10, "color": TEXT_3},
            "nticks": 5,
            "automargin": True,
        },
    }


def _rel_band(
    pts: list[tuple[date, float | None]], check_id: str
) -> tuple[list[date], list[float], list[float]]:
    """Warning / critical floors per point, vs the point four quarters earlier."""
    xs: list[date] = []
    warn: list[float] = []
    crit: list[float] = []
    for i, (d, _v) in enumerate(pts):
        if i < 4:
            continue
        prior = pts[i - 4][1]
        if prior is None:
            continue
        if check_id == "A4_CAPRATE":
            w = prior + t("CAPRATE_WARN_PP")
            c = prior + t("CAPRATE_CRIT_PP")
        else:
            w = prior + max(t("DSO_WARN_DAYS"), t("DSO_WARN_REL") * abs(prior))
            c = prior + max(t("DSO_CRIT_DAYS"), t("DSO_CRIT_REL") * abs(prior))
        xs.append(d)
        warn.append(w)
        crit.append(c)
    return xs, warn, crit


def _add_zones(
    fig: go.Figure,
    bx: list[date],
    warn: list[float],
    crit: list[float],
    hi: float,
    fmt: str,
    unit: str,
) -> float:
    """Shared WARNING / CRITICAL zone traces, so every chart has the same legend."""
    peak = max(hi, max(crit))
    top = peak * 1.08 if peak > 0 else max(crit) + abs(max(crit)) * 0.25 + 1e-9
    fig.add_trace(
        go.Scatter(
            x=bx,
            y=warn,
            mode="lines",
            line={"color": WARN_LINE, "width": 1, "dash": "dot"},
            name="WARNING zone",
            hovertemplate="warn ≥ %{y:" + fmt + "}" + unit + "<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=bx,
            y=crit,
            mode="lines",
            fill="tonexty",
            fillcolor=WARN_FILL,
            line={"color": CRIT_LINE, "width": 1, "dash": "dot"},
            name="CRITICAL zone",
            hovertemplate="crit ≥ %{y:" + fmt + "}" + unit + "<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=bx,
            y=[top] * len(bx),
            mode="lines",
            fill="tonexty",
            fillcolor=CRIT_FILL,
            line={"width": 0},
            showlegend=False,
            hoverinfo="skip",
        )
    )
    return max(hi, top)


def check_figure(check_id: str, title: str, history: Sequence[Any]) -> go.Figure | None:
    """Line chart of the last 12 quarters plus the threshold band, or None if
    there are fewer than two numeric points."""
    pts = history_points(history)
    numeric = [(d, v) for d, v in pts if v is not None]
    if len(numeric) < 2:
        return None
    spec = _SPEC.get(check_id, {"unit": "", "fmt": ",.2f", "band": None})
    fmt, unit = spec["fmt"], spec["unit"]
    fig = go.Figure()
    xs = [d for d, _ in pts]
    ys = [v for _, v in pts]
    hi = max(v for _, v in numeric)
    lo = min(v for _, v in numeric)

    band = spec.get("band")
    bx: list[date] = []
    warn: list[float] = []
    crit: list[float] = []
    if band in ("rel_days", "rel_pp"):
        bx, warn, crit = _rel_band(pts, check_id)
    elif band == "abs_level":
        bx = list(xs)
        warn = [t("ACCRUAL_WARN")] * len(bx)
        crit = [t("ACCRUAL_CRIT")] * len(bx)
    if bx:
        hi = _add_zones(fig, bx, warn, crit, hi, fmt, unit)

    fig.add_trace(
        go.Scatter(
            x=xs,
            y=ys,
            mode="lines+markers",
            name=title[:40] or check_id,
            connectgaps=False,
            line={"color": ACCENT, "width": 2.4},
            marker={"size": 5, "color": ACCENT, "line": {"width": 0}},
            hovertemplate="%{x|%Y-%m-%d}  %{y:" + fmt + "}" + unit + "<extra></extra>",
        )
    )
    last_d, last_v = numeric[-1]
    fig.add_trace(
        go.Scatter(
            x=[last_d],
            y=[last_v],
            mode="markers",
            showlegend=False,
            hoverinfo="skip",
            marker={
                "size": 13,
                "color": "rgba(90,200,250,0.18)",
                "line": {"width": 2, "color": ACCENT},
            },
        )
    )
    layout = _base_layout(fmt, unit)
    pad = (hi - lo) * 0.12 or abs(hi) * 0.1 or 1.0
    layout["yaxis"]["range"] = [lo - pad, hi + pad * 0.2]
    fig.update_layout(**layout)
    return fig
