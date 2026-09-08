"""
Layout system: design tokens, text fitting, and the one-page reduction ladder.

ARCHITECTURAL DECISION -- the page budget is data, not code
-----------------------------------------------------------
"Must be exactly one page" is a hard constraint that cannot be satisfied by
careful estimation, because content length is unknown until render time. So the
generator renders, measures, and if it overflowed, re-renders with the next
`LayoutBudget` from `LADDER`.

Each rung of the ladder encodes the spec's stated priority order:
    1. shorten narrative
    2. reduce the number of competitors displayed
    3. prioritise investor-relevant information
    4. never overflow
The early rungs trim prose; the middle rungs drop competitor rows; the last
rungs sacrifice whole sections, lowest investor value first. Because the ladder
is a list of plain dataclasses, what gets cut and in what order is auditable
and testable without rendering anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth

# --------------------------------------------------------------------------
# Page geometry (US Letter, portrait)
# --------------------------------------------------------------------------

PAGE_W, PAGE_H = letter          # 612 x 792 pt
MARGIN_X = 32.0
MARGIN_TOP = 30.0
MARGIN_BOTTOM = 26.0
CONTENT_W = PAGE_W - 2 * MARGIN_X
CONTENT_TOP = PAGE_H - MARGIN_TOP
CONTENT_BOTTOM = MARGIN_BOTTOM + 14.0    # leaves room for the footer rule

# --------------------------------------------------------------------------
# Typography -- built-in Type 1 faces only, so the app has no font-file
# dependency and renders identically on any machine.
# --------------------------------------------------------------------------

FONT = "Helvetica"
FONT_B = "Helvetica-Bold"
FONT_O = "Helvetica-Oblique"

# --------------------------------------------------------------------------
# Palette -- institutional research: near-black text, one accent, grey rules.
# Saturated colour is used only where it carries meaning (threat, advantage).
# --------------------------------------------------------------------------

INK = colors.HexColor("#111827")
INK_SOFT = colors.HexColor("#374151")
MUTED = colors.HexColor("#6B7280")
FAINT = colors.HexColor("#9CA3AF")
RULE = colors.HexColor("#D1D5DB")
RULE_SOFT = colors.HexColor("#E5E7EB")
BAND = colors.HexColor("#F3F4F6")
ACCENT = colors.HexColor("#1E354F")          # TEN Capital navy-700
ACCENT_SOFT = colors.HexColor("#E8EDF3")
WHITE = colors.white

HIGH = colors.HexColor("#B91C1C")            # high threat / risk
MEDIUM = colors.HexColor("#B45309")          # medium
LOW = colors.HexColor("#15803D")             # low / company advantage

SEVERITY_COLOR = {"high": HIGH, "medium": MEDIUM, "low": LOW}

#: The brand's three figures, used for the signature rule under the header.
#: The interface carries the same gradient; on paper it is three solid segments
#: because a print gradient bands badly at this thickness.
BRAND_CORAL = colors.HexColor("#ED5644")
BRAND_AMBER = colors.HexColor("#F1A31F")
BRAND_TEAL = colors.HexColor("#4FC4D6")
BRAND_RULE = (BRAND_CORAL, BRAND_AMBER, BRAND_TEAL)
THREAT_COLOR = {"high": HIGH, "medium": MEDIUM, "low": LOW}


def score_color(total: int) -> colors.Color:
    """Colour for the differentiation score, matching the spec's bands."""
    if total >= 65:
        return LOW
    if total >= 50:
        return MEDIUM
    return HIGH


# --------------------------------------------------------------------------
# Text measurement and fitting
# --------------------------------------------------------------------------


def text_width(text: str, font: str = FONT, size: float = 8.0) -> float:
    return stringWidth(text or "", font, size)


def truncate(text: str, max_width: float, font: str = FONT, size: float = 8.0) -> str:
    """Hard-truncate to a pixel width, with an ellipsis.

    Used for table cells, where wrapping would break the row grid. Everywhere
    else prefers `wrap`, because losing the end of a sentence is worse than
    using another line.
    """
    text = (text or "").strip()
    if not text:
        return ""
    if text_width(text, font, size) <= max_width:
        return text
    ellipsis = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if text_width(text[:mid] + ellipsis, font, size) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo].rstrip() + ellipsis) if lo else ellipsis


def wrap(text: str, max_width: float, font: str = FONT, size: float = 8.0) -> List[str]:
    """Greedy word wrap to a pixel width."""
    words = (text or "").split()
    if not words:
        return []
    lines: List[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if text_width(trial, font, size) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def wrap_limited(
    text: str,
    max_width: float,
    max_lines: int,
    font: str = FONT,
    size: float = 8.0,
) -> List[str]:
    """Wrap, then hard-cap the line count with an ellipsis on the last line."""
    lines = wrap(text, max_width, font, size)
    if len(lines) <= max_lines:
        return lines
    kept = lines[: max_lines - 1] if max_lines > 1 else []
    remainder = " ".join(lines[max_lines - 1:])
    kept.append(truncate(remainder, max_width, font, size))
    return kept


def sentence_clip(text: str, max_chars: int) -> str:
    """Trim prose to a length, preferring to end on a sentence boundary.

    The narrative-shortening rungs of the ladder use this rather than a blunt
    character cut, so a shortened assessment still reads as finished prose.
    """
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    window = text[: max_chars + 1]
    for stop in (". ", "; ", " — ", ", "):
        idx = window.rfind(stop)
        if idx > max_chars * 0.55:
            return window[: idx + 1].strip()
    idx = window.rfind(" ")
    return (window[:idx] if idx > 0 else window).rstrip(" ,;:—-") + "…"


# --------------------------------------------------------------------------
# The reduction ladder
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LayoutBudget:
    """One rung: how much of each section is allowed onto the page."""

    name: str
    #: Competitor rows in the matrix (excluding the subject company's own row).
    matrix_rows: int = 6
    #: Characters allowed for the overall assessment paragraph.
    assessment_chars: int = 330
    #: Whitespace opportunities shown.
    whitespace_items: int = 3
    #: Lines of supporting text per whitespace item.
    whitespace_lines: int = 2
    #: Investor watchlist entries shown.
    concern_items: int = 5
    #: Lines per watchlist entry (risk / question / evidence are packed).
    concern_lines: int = 3
    #: Whether the market map is drawn at all.
    show_market_map: bool = True
    #: Height of the market map panel.
    map_height: float = 132.0
    #: Whether the score's category breakdown strip is drawn.
    show_score_breakdown: bool = True
    #: Whether the "claims to avoid" line under positioning is drawn.
    show_claims_to_avoid: bool = True
    #: Base body font size; the last resort, applied only after content cuts.
    body_size: float = 7.6
    #: Vertical gap between sections.
    section_gap: float = 9.0


#: Ordered most generous to most aggressive. The generator walks this list.
LADDER: List[LayoutBudget] = [
    # 0 — full report.
    LayoutBudget(name="full"),
    # 1 — shorten the narrative (spec priority 1).
    LayoutBudget(
        name="tighter-prose",
        assessment_chars=250,
        whitespace_lines=2,
        concern_lines=2,
        section_gap=8.0,
    ),
    # 2 — fewer competitors (spec priority 2).
    LayoutBudget(
        name="fewer-competitors",
        matrix_rows=5,
        assessment_chars=230,
        whitespace_lines=2,
        concern_lines=2,
        map_height=122.0,
        section_gap=7.5,
    ),
    # 3 — fewer competitors still, drop the score breakdown strip.
    LayoutBudget(
        name="minimum-competitors",
        matrix_rows=4,
        assessment_chars=200,
        whitespace_items=3,
        whitespace_lines=1,
        concern_items=4,
        concern_lines=2,
        show_score_breakdown=False,
        map_height=112.0,
        section_gap=7.0,
        body_size=7.4,
    ),
    # 4 — prioritise investor-relevant content: the watchlist outranks the
    #     whitespace list, which outranks the map.
    LayoutBudget(
        name="drop-map",
        matrix_rows=4,
        assessment_chars=180,
        whitespace_items=3,
        whitespace_lines=1,
        concern_items=4,
        concern_lines=2,
        show_market_map=False,
        show_score_breakdown=False,
        show_claims_to_avoid=False,
        section_gap=7.0,
        body_size=7.3,
    ),
    # 5 — last resort: the decision-critical core only.
    LayoutBudget(
        name="core-only",
        matrix_rows=4,
        assessment_chars=150,
        whitespace_items=2,
        whitespace_lines=1,
        concern_items=3,
        concern_lines=2,
        show_market_map=False,
        show_score_breakdown=False,
        show_claims_to_avoid=False,
        section_gap=6.0,
        body_size=7.2,
    ),
]
