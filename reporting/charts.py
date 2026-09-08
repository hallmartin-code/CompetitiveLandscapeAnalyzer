"""
Charts, drawn directly onto the page canvas.

Deliberately hand-drawn with canvas primitives rather than built with a
charting library. At this size -- a 130pt-tall panel on a dense one-pager --
library defaults (axis padding, tick labels, legends, title space) consume most
of the box and leave the data illegible. Drawing directly gives exact control
over every point of vertical budget, which is the scarce resource here.

Only two charts exist, and each earns its place by answering a question faster
than text could: "how differentiated, on what scale?" and "who sits where?".
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from models.schemas import MarketMap
from reporting.layout import (
    ACCENT,
    BAND,
    FAINT,
    FONT,
    FONT_B,
    HIGH,
    INK_SOFT,
    LOW,
    MEDIUM,
    MUTED,
    RULE,
    RULE_SOFT,
    WHITE,
    score_color,
    text_width,
    truncate,
)


def draw_score_bar(
    c,
    x: float,
    y: float,
    width: float,
    total: int,
    height: float = 7.0,
    template: bool = False,
) -> float:
    """A 0-100 band strip with a marker at the score. Returns height consumed.

    The band boundaries (35/50/65/80) are drawn because the number alone is
    meaningless without the scale the spec defines.
    """
    boundaries = [0, 35, 50, 65, 80, 100]
    fills = [
        HIGH.clone(alpha=1), HIGH.clone(alpha=1), MEDIUM.clone(alpha=1),
        LOW.clone(alpha=1), LOW.clone(alpha=1),
    ]
    # Progressively lighter tints so the strip reads as a scale, not a warning.
    tints = [0.30, 0.45, 0.42, 0.40, 0.62]

    for i in range(len(boundaries) - 1):
        x0 = x + width * boundaries[i] / 100.0
        x1 = x + width * boundaries[i + 1] / 100.0
        fill = fills[i].clone()
        fill.alpha = tints[i]
        c.setFillColor(fill)
        c.rect(x0, y, x1 - x0, height, stroke=0, fill=1)

    # Boundary ticks with their values.
    c.setFont(FONT, 4.6)
    c.setFillColor(FAINT)
    for b in boundaries[1:-1]:
        bx = x + width * b / 100.0
        c.setStrokeColor(WHITE)
        c.setLineWidth(0.6)
        c.line(bx, y, bx, y + height)
        c.drawCentredString(bx, y - 5.4, str(b))

    # Score marker. In template mode it sits mid-scale in neutral grey: the
    # marker's position is part of the structure, its value is not.
    position = 50 if template else max(0, min(100, total))
    mx = x + width * position / 100.0
    c.setFillColor(FAINT if template else score_color(total))
    c.setStrokeColor(WHITE)
    c.setLineWidth(1.0)
    c.circle(mx, y + height / 2.0, 3.6, stroke=1, fill=1)

    return height + 7.0


def draw_category_strip(
    c,
    x: float,
    y: float,
    width: float,
    categories: Sequence,
    size: float = 5.4,
) -> float:
    """Per-category achievement bars, widths proportional to category weight.

    Weight-proportional widths matter: they show at a glance that a weak score
    on a 20-point category costs more than a weak score on a 10-point one.
    """
    if not categories:
        return 0.0

    total_weight = sum(cat.weight for cat in categories) or 1
    gap = 2.4
    available = width - gap * (len(categories) - 1)
    bar_h = 4.2
    label_y = y
    bar_y = y + size + 1.6

    cx = x
    for cat in categories:
        w = available * cat.weight / total_weight
        # Track.
        c.setFillColor(RULE_SOFT)
        c.rect(cx, bar_y, w, bar_h, stroke=0, fill=1)
        # Achievement.
        fill = score_color(int(round(cat.raw * 100)))
        c.setFillColor(fill)
        c.rect(cx, bar_y, w * max(0.0, min(1.0, cat.raw)), bar_h, stroke=0, fill=1)

        c.setFont(FONT, size)
        c.setFillColor(MUTED)
        label = _short_category(cat.name)
        c.drawString(cx, label_y, truncate(label, w, FONT, size))
        cx += w + gap

    return bar_y + bar_h - y + 2.0


_CATEGORY_SHORT = {
    "Product / Technology": "Product/Tech",
    "Customer Value Proposition": "Cust. value",
    "Competitive Performance": "Performance",
    "Distribution / GTM": "Distribution",
    "Proprietary Data / IP": "Data/IP",
    "Switching Costs / Network Effects": "Switching/Network",
    "Market Positioning": "Positioning",
    "Sustainability of Advantage": "Durability",
}


def _short_category(name: str) -> str:
    return _CATEGORY_SHORT.get(name, name)


# --------------------------------------------------------------------------
# Market map
# --------------------------------------------------------------------------


def draw_market_map(
    c,
    x: float,
    y: float,
    width: float,
    height: float,
    market_map: MarketMap,
    max_points: int = 12,
) -> None:
    """Scatter plot of the competitive positions.

    Label collision is resolved by nudging labels around each point rather than
    by dropping them: an unlabelled dot on a market map carries no information.
    """
    pad_l, pad_r, pad_t, pad_b = 30.0, 8.0, 9.0, 22.0
    plot_x = x + pad_l
    plot_y = y + pad_b
    plot_w = max(20.0, width - pad_l - pad_r)
    plot_h = max(20.0, height - pad_t - pad_b)

    # Plot field.
    c.setFillColor(BAND)
    c.rect(plot_x, plot_y, plot_w, plot_h, stroke=0, fill=1)

    # Quadrant guides.
    c.setStrokeColor(WHITE)
    c.setLineWidth(0.7)
    c.line(plot_x + plot_w / 2, plot_y, plot_x + plot_w / 2, plot_y + plot_h)
    c.line(plot_x, plot_y + plot_h / 2, plot_x + plot_w, plot_y + plot_h / 2)

    c.setStrokeColor(RULE)
    c.setLineWidth(0.5)
    c.rect(plot_x, plot_y, plot_w, plot_h, stroke=1, fill=0)

    # Axis labels.
    c.setFont(FONT_B, 5.2)
    c.setFillColor(INK_SOFT)
    c.drawString(plot_x, y + 10.0, truncate(market_map.axis_x.label, plot_w * 0.6, FONT_B, 5.2))
    c.setFont(FONT, 4.8)
    c.setFillColor(FAINT)
    c.drawString(plot_x, y + 3.4, truncate(market_map.axis_x.low_label, plot_w * 0.42, FONT, 4.8))
    right = truncate(market_map.axis_x.high_label, plot_w * 0.42, FONT, 4.8)
    c.drawRightString(plot_x + plot_w, y + 3.4, right)

    # Y axis, rotated.
    c.saveState()
    c.translate(x + 7.6, plot_y)
    c.rotate(90)
    c.setFont(FONT_B, 5.2)
    c.setFillColor(INK_SOFT)
    c.drawString(0, 0, truncate(market_map.axis_y.label, plot_h * 0.7, FONT_B, 5.2))
    c.restoreState()

    c.saveState()
    c.translate(x + 14.0, plot_y)
    c.rotate(90)
    c.setFont(FONT, 4.8)
    c.setFillColor(FAINT)
    c.drawString(0, 0, truncate(market_map.axis_y.low_label, plot_h * 0.4, FONT, 4.8))
    c.drawRightString(plot_h, 0, truncate(market_map.axis_y.high_label, plot_h * 0.4, FONT, 4.8))
    c.restoreState()

    # Points: subject last so it draws on top.
    entries = sorted(market_map.entries, key=lambda e: e.is_company)[-max_points:]
    placed: List[Tuple[float, float, float, float]] = []

    for entry in entries:
        px = plot_x + 5 + (plot_w - 10) * entry.x
        py = plot_y + 5 + (plot_h - 10) * entry.y
        is_subject = entry.is_company

        size = 3.5 if is_subject else 2.1
        if is_subject:
            c.setFillColor(ACCENT)
            c.setStrokeColor(WHITE)
            c.setLineWidth(1.1)
            c.circle(px, py, size, stroke=1, fill=1)
        else:
            c.setFillColor(MUTED)
            c.circle(px, py, size, stroke=0, fill=1)

        font = FONT_B if is_subject else FONT
        fsize = 5.0 if is_subject else 4.5
        label = truncate(_short_name(entry.name), 62, font, fsize)
        lw = text_width(label, font, fsize)

        lx, ly = _place_label(px, py, lw, fsize, plot_x, plot_y, plot_w, plot_h, placed)
        placed.append((lx, ly, lw, fsize))

        c.setFont(font, fsize)
        c.setFillColor(ACCENT if is_subject else INK_SOFT)
        c.drawString(lx, ly, label)


def _short_name(name: str) -> str:
    """Trim a competitor label to something that fits a scatter point."""
    name = (name or "").split("(")[0].strip()
    for sep in (":", " — ", " - ", "/"):
        if sep in name:
            head = name.split(sep)[0].strip()
            if len(head) >= 4:
                name = head
                break
    words = name.split()
    if len(words) > 3:
        name = " ".join(words[:3])
    return name[:26]


def _place_label(
    px: float,
    py: float,
    lw: float,
    fsize: float,
    plot_x: float,
    plot_y: float,
    plot_w: float,
    plot_h: float,
    placed: Sequence[Tuple[float, float, float, float]],
) -> Tuple[float, float]:
    """Pick the first candidate offset that neither collides nor leaves the box."""
    candidates = [
        (5.0, -1.6), (-lw - 5.0, -1.6), (5.0, 4.2), (-lw - 5.0, 4.2),
        (5.0, -7.0), (-lw - 5.0, -7.0), (-lw / 2, 5.6), (-lw / 2, -8.4),
    ]
    for dx, dy in candidates:
        lx, ly = px + dx, py + dy
        if lx < plot_x - 2 or lx + lw > plot_x + plot_w + 2:
            continue
        if ly < plot_y - 1 or ly + fsize > plot_y + plot_h + 1:
            continue
        if not any(_overlaps(lx, ly, lw, fsize, *p) for p in placed):
            return lx, ly
    # Every candidate collided: accept the first in-bounds one rather than
    # dropping the label.
    for dx, dy in candidates:
        lx, ly = px + dx, py + dy
        if plot_x - 2 <= lx and lx + lw <= plot_x + plot_w + 2:
            return lx, max(plot_y, min(ly, plot_y + plot_h - fsize))
    return px + 5.0, py - 1.6


def _overlaps(
    ax: float, ay: float, aw: float, ah: float,
    bx: float, by: float, bw: float, bh: float,
) -> bool:
    pad = 1.0
    return not (
        ax + aw + pad < bx or bx + bw + pad < ax or ay + ah + pad < by or by + bh + pad < ay
    )
