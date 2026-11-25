"""HTML builders for the plain-English layer (landing story, verdict panel,
explainers, glossary tips). Same rules as components.py: escape everything,
single-line markup, no Streamlit dependency."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from veritas.ui.components import esc, one_line, sev_class, tag
from veritas.ui.plain import (
    CHECK_PLAIN,
    GLOSSARY,
    MODULE_PLAIN,
    SEVERITY_PLAIN,
    Verdict,
)

# ---------------------------------------------------------------------------
# Glossary tooltips
# ---------------------------------------------------------------------------


def term(text: str, key: str | None = None) -> str:
    """Dotted-underline jargon with a hover / focus definition."""
    tip = GLOSSARY.get(key or text)
    if not tip:
        return esc(text)
    return (
        f'<span class="vx-term" tabindex="0" aria-label="{esc(text)}: {esc(tip)}" '
        f'data-tip="{esc(tip)}">'
        f"{esc(text)}</span>"
    )


def glossary_html(entries: Mapping[str, str] | None = None) -> str:
    rows = "".join(
        f'<div class="vx-gloss-row"><b>{esc(k)}</b><span>{esc(v)}</span></div>'
        for k, v in (entries if entries is not None else GLOSSARY).items()
    )
    return one_line(f'<div class="vx vx-gloss">{rows}</div>')


# ---------------------------------------------------------------------------
# Landing
# ---------------------------------------------------------------------------


def hero() -> str:
    return one_line(
        '<div class="vx vx-hero">'
        '<div class="vx-label vx-hero-kicker">A second look at company reports</div>'
        '<div class="vx-wordmark">VERITAS<span>.</span></div>'
        '<div class="vx-tagline"><em>What companies say.</em> <b>What their numbers show.</b>'
        "</div>"
        '<div class="vx-lede">Every public company in the U.S. files reports with the '
        f"government ({term('SEC')}). In them, managers tell a story about how the business "
        "is going, and publish the numbers behind it. VERITAS reads both side by side and "
        "shows you, in plain English, where they don't line up. Every quote and every number "
        "links back to the original filing.</div></div>"
    )


def story_steps() -> str:
    """Three-step explainer with an illustrative (clearly labelled) example."""
    steps = (
        (
            "1",
            "Read what they said",
            "We pull the managers' own words from annual and quarterly reports and "
            "earnings releases.",
            '<div class="vx-step-demo is-quote"><em class="vx-demo-tag">Example</em>'
            "“Demand was exceptionally strong this quarter.”</div>",
        ),
        (
            "2",
            "Check the receipts",
            "We line those words up with the exact figures the company reported for the "
            "same period.",
            '<div class="vx-step-demo is-numbers"><em class="vx-demo-tag">Example</em>'
            '<span>Sales</span><b class="up">↑ +12%</b>'
            '<span>Cash collected</span><b class="down">↓ −8%</b></div>',
        ),
        (
            "3",
            "Show where they disagree",
            "Gaps are flagged, explained in plain English, and linked to the source so you can "
            "check them yourself.",
            '<div class="vx-step-demo is-flag"><em class="vx-demo-tag">Example</em><i>≠</i>'
            "<span>Worth a closer look</span></div>",
        ),
    )
    cells = "".join(
        f'<div class="vx-step" style="--i:{i}"><div class="vx-step-num">{n}</div>'
        f'<div class="vx-step-title">{esc(t)}</div><div class="vx-step-body">{esc(b)}</div>'
        f"{demo}</div>"
        for i, (n, t, b, demo) in enumerate(steps)
    )
    return one_line(
        '<div class="vx vx-steps-wrap"><div class="vx-steps">'
        f"{cells}</div>"
        '<div class="vx-steps-note">The examples above are made up to show the idea. Pick a '
        "company to see real results from real filings.</div>"
        "</div>"
    )


SEED_HOOKS: dict[str, str] = {
    "NVDA": "The AI chip maker",
    "WMT": "The world's largest retailer",
    "TSLA": "Electric cars and batteries",
    "BA": "Commercial jets and defense",
}


def seed_meta(ticker: str, name: str) -> str:
    hook = SEED_HOOKS.get(ticker, "")
    return one_line(f'<div class="vx seed-meta"><b>{esc(name)}</b><span>{esc(hook)}</span></div>')


def how_it_works(simple: bool = False) -> str:
    cells = []
    for i, key in enumerate(("A", "B", "C", "D")):
        question, pro = MODULE_PLAIN[key]
        body = {
            "A": "Five checks on the numbers (how fast customers pay, whether unsold goods "
            "pile up, whether profit turns into cash...), each set next to what managers said "
            "about the same quarter.",
            "B": "Companies update the detailed notes under their financial statements every "
            "year, mostly for routine reasons. We compare them sentence by sentence and "
            "highlight added, removed or softened sentences worth reading.",
            "C": "People manage toward what they are paid for. We find which results the top "
            "bosses' bonuses depend on, and flag pay targets that could be lifted by paying "
            "staff in company shares or by buying other businesses.",
            "D": "Everything above rolls up into a 0–100 score. Checks we could not run "
            "are listed, never counted as a pass.",
        }[key]
        cells.append(
            f'<div class="vx-module" style="--i:{i}"><div class="vx-module-letter">{key}</div>'
            f"<div class='vx-h4'>{esc(question)}</div>"
            f"<div class='vx-p'>{esc(body)}</div>{'' if simple else tag(pro)}</div>"
        )
    return one_line(
        '<div class="vx"><div class="vx-section-kicker"><span class="vx-label">Four questions '
        f'we ask every company</span></div><div class="vx-modules">{"".join(cells)}</div></div>'
    )


def pro_hints() -> str:
    hints = "".join(tag(h) for h in ("NVDA", "CIK 320193", "DIFF", "CONTRA", "EXPORT"))
    return one_line(
        '<div class="vx vx-hints is-pro"><span class="vx-label" style="padding-top:4px">'
        f"For analysts: tickers, CIKs and commands</span>{hints}</div>"
    )


# ---------------------------------------------------------------------------
# Company page: the short version
# ---------------------------------------------------------------------------


_VIEW_NAMES: dict[bool, dict[str, str]] = {
    True: {
        "below": "below",
        "pay": "the Executive pay tab",
        "fine print": "below and on the Fine-print changes tab",
    },
    False: {"below": "below", "pay": "the Proxy tab", "fine print": "the Redline tab"},
}


def _point_card(p: Any, i: int, simple: bool = True) -> str:
    sev = sev_class(p.severity)
    where = _VIEW_NAMES[simple].get(p.view, p.view)
    analogy = f'<div class="vx-point-analogy">{esc(p.analogy)}</div>' if p.analogy else ""
    return (
        f'<div class="vx-point {sev}" style="--i:{i}"><div class="vx-point-head">'
        f'<span class="vx-point-sev"><i></i>{esc(SEVERITY_PLAIN.get(p.severity, ""))}</span>'
        f'<span class="vx-point-view">Evidence: {esc(where)}</span></div>'
        f'<div class="vx-point-title">{esc(p.title)}</div>'
        f'<div class="vx-point-text">{esc(p.text)}</div>{analogy}</div>'
    )


def verdict_panel(v: Verdict, company: str, score_band: str, simple: bool = True) -> str:
    points = "".join(_point_card(p, i, simple) for i, p in enumerate(v.points))
    if not v.points:
        points = (
            '<div class="vx-point noflag" style="--i:0"><div class="vx-point-head">'
            '<span class="vx-point-sev"><i></i>In line</span></div>'
            '<div class="vx-point-title">Nothing stood out</div>'
            '<div class="vx-point-text">None of the scored checks we could run raised a flag. '
            "That is not a guarantee: it means this screen found no gap.</div></div>"
        )
    extras = ""
    if v.good:
        extras += (
            '<div class="vx-verdict-row is-good"><span class="vx-label">No flag raised</span>'
            f"<span>{esc(', '.join(v.good))}</span></div>"
        )
    if v.empty:
        extras += (
            '<div class="vx-verdict-row is-na"><span class="vx-label">Nothing to check</span>'
            f"<span>{esc('; '.join(v.empty))}</span></div>"
        )
    if v.not_applicable:
        extras += (
            '<div class="vx-verdict-row is-na"><span class="vx-label">Left out</span>'
            f"<span>{esc('; '.join(v.not_applicable))}</span></div>"
        )
    if v.unchecked:
        extras += (
            '<div class="vx-verdict-row is-na"><span class="vx-label">Couldn\'t check</span>'
            f"<span>{esc('; '.join(v.unchecked))}</span></div>"
        )
    return one_line(
        f'<div class="vx vx-short band-{esc(score_band)}">'
        '<div class="vx-short-head"><span class="vx-label">The short version</span>'
        f'<span class="vx-label vx-short-co">{esc(company)}</span></div>'
        f'<div class="vx-short-headline">{esc(v.headline)}</div>'
        f'<div class="vx-short-summary">{esc(v.summary)}</div>'
        f'<div class="vx-points">{points}</div>{extras}'
        '<div class="vx-short-foot">Scroll down for the evidence: each quote and number, with '
        "links to the filing it came from.</div></div>"
    )


# ---------------------------------------------------------------------------
# Explainers for the deeper views
# ---------------------------------------------------------------------------


def view_intro(title: str, body: str) -> str:
    return one_line(
        f'<div class="vx vx-intro"><span class="vx-intro-tag">{esc(title)}</span>'
        f"<span>{esc(body)}</span></div>"
    )


VIEW_INTROS: dict[str, tuple[str, str]] = {
    "overview_simple": (
        "How to read this",
        "Each card below explains one finding in plain English: what the numbers show, what "
        "managers said (if we found a statement to compare), and links to the report so you "
        "can check it yourself. Red means a big gap, amber means worth a closer look, green "
        "means no flag.",
    ),
    "overview": (
        "How to read this",
        "Each card opens with a plain-English line, then puts what managers wrote next to the "
        "numbers for the same period. A ≠ sign marks a statement and a measure that sit "
        "uneasily together. Red is a big gap, amber is worth a closer look, green raised no "
        "flag.",
    ),
    "redline": (
        "What you are looking at",
        "Under the financial statements, companies publish detailed notes explaining risks, "
        "debts and lawsuits. We compare this year's notes with last year's, sentence by "
        "sentence. Struck-through red text was removed; green text was added.",
    ),
    "ratios_simple": (
        "What you are looking at",
        "The same number checks as on the Summary tab, tracked over the last three years. The "
        "shaded areas show where a value would be flagged.",
    ),
    "ratios": (
        "What you are looking at",
        "The same checks as on the Overview, tracked over the last three years. The shaded "
        "zones show where a value would be flagged.",
    ),
    "proxy_simple": (
        "What you are looking at",
        "Every year companies must explain to shareholders how their top executives are "
        "paid. We find which results their bonuses depend on, because people tend to manage "
        "toward what they are paid for.",
    ),
    "proxy": (
        "What you are looking at",
        "Every year companies must explain how their top executives are paid (the proxy "
        "statement). We find which numbers their bonuses depend on, because people tend to "
        "manage toward what they are paid for.",
    ),
}


def check_explainer(check_id: str) -> str:
    cp = CHECK_PLAIN.get(check_id)
    if cp is None:
        return ""
    return one_line(
        f'<div class="vx vx-explainer"><div class="vx-explainer-name">{esc(cp.name)}</div>'
        f'<div class="vx-explainer-q">{esc(cp.question)}</div>'
        f'<div class="vx-explainer-why">{esc(cp.why)}</div>'
        f'<div class="vx-point-analogy">{esc(cp.analogy)}</div></div>'
    )


def edit_plain_line(text: str) -> str:
    return one_line(
        f'<div class="vx vx-plain is-inline"><span class="vx-plain-tag">In plain English</span>'
        f"<span>{esc(text)}</span></div>"
    )


def identity_tips(facts: Sequence[tuple[str, Any]]) -> Mapping[str, str]:
    """Glossary tips for identity labels that have one."""
    return {k: GLOSSARY[k] for k, _ in facts if k in GLOSSARY}
