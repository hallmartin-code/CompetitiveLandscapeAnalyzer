"""
One-page investor PDF (spec sections 13 and 14).

ARCHITECTURAL DECISION -- render, measure, retry
------------------------------------------------
Content length is unknowable before render time, so "exactly one page" is
enforced by a loop rather than by estimation:

    for budget in LADDER:
        try to render at this budget
        if any section would cross the bottom margin -> _Overflow -> next rung
    if every rung overflowed -> render the last rung with hard clipping and
    report the failure loudly in diagnostics

The renderer is written against a plain `canvas`, not Platypus flowables,
because the constraint here is a fixed *page budget* rather than a document
flow. With a canvas, every section knows exactly how much vertical space it has
left (`self.remaining`) and can decide whether it fits before drawing anything.
Platypus would happily start page two.

`self.require(h)` is the single choke point: nothing is drawn without first
reserving its height, so overflow is detected before ink hits the page rather
than discovered afterwards.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from reportlab.pdfgen import canvas as pdfcanvas

from config import (
    FOOTER_LEFT,
    FOOTER_RIGHT,
    REPORT_TITLE,
    Settings,
    settings as default_settings,
)
from models.schemas import (
    NOT_DISCLOSED,
    UNKNOWN,
    CompetitiveAnalysis,
    Competitor,
    MatrixRow,
)
from reporting.charts import draw_category_strip, draw_market_map, draw_score_bar
from reporting.layout import (
    ACCENT,
    ACCENT_SOFT,
    BAND,
    BRAND_RULE,
    CONTENT_BOTTOM,
    CONTENT_TOP,
    CONTENT_W,
    FAINT,
    FONT,
    FONT_B,
    FONT_O,
    INK,
    INK_SOFT,
    LADDER,
    MARGIN_X,
    MUTED,
    PAGE_H,
    PAGE_W,
    RULE,
    RULE_SOFT,
    SEVERITY_COLOR,
    THREAT_COLOR,
    WHITE,
    LayoutBudget,
    score_color,
    sentence_clip,
    text_width,
    truncate,
    wrap_limited,
)

logger = logging.getLogger(__name__)


class _Overflow(Exception):
    """Raised the moment a section will not fit in the remaining page budget."""


#: Matrix column widths, summing to CONTENT_W (548pt).
_MATRIX_COLS: List[Tuple[str, float]] = [
    ("Company", 78.0),
    ("Customer", 64.0),
    ("Product / approach", 92.0),
    ("Pricing", 50.0),
    ("Traction", 60.0),
    ("Distribution", 58.0),
    ("Primary advantage", 106.0),
    ("Threat", 40.0),
]


class _Renderer:
    """Draws one report at one budget. Single use."""

    def __init__(
        self,
        analysis: CompetitiveAnalysis,
        budget: LayoutBudget,
        settings: Settings,
        template: bool = False,
    ) -> None:
        self.a = analysis
        self.b = budget
        self.settings = settings
        #: Template mode renders the blank structural skeleton. Numerals become
        #: placeholders and the score marker is neutral, so a template can never
        #: be mistaken for a report about a company that scored zero.
        self.template = template
        self.y = CONTENT_TOP
        self.buffer = io.BytesIO()
        self.c = pdfcanvas.Canvas(self.buffer, pagesize=(PAGE_W, PAGE_H))

    # -- vertical budget ---------------------------------------------------

    @property
    def remaining(self) -> float:
        return self.y - CONTENT_BOTTOM

    def require(self, height: float) -> float:
        """Reserve vertical space, or abort this rung. Returns the top y."""
        if self.y - height < CONTENT_BOTTOM:
            raise _Overflow(
                f"needed {height:.1f}pt, only {self.remaining:.1f}pt left "
                f"(budget '{self.b.name}')"
            )
        top = self.y
        self.y -= height
        return top

    def gap(self, height: Optional[float] = None) -> None:
        self.y -= self.b.section_gap if height is None else height

    # -- primitives --------------------------------------------------------

    def section_header(self, number: int, title: str, note: str = "") -> None:
        h = 13.0
        top = self.require(h)
        c = self.c
        baseline = top - 8.4

        c.setFont(FONT_B, 5.6)
        c.setFillColor(WHITE)
        c.setStrokeColor(ACCENT)
        c.setFillColor(ACCENT)
        c.rect(MARGIN_X, baseline - 1.6, 9.6, 8.0, stroke=0, fill=1)
        c.setFillColor(WHITE)
        c.drawCentredString(MARGIN_X + 4.8, baseline + 0.7, str(number))

        c.setFont(FONT_B, 8.2)
        c.setFillColor(ACCENT)
        c.drawString(MARGIN_X + 14.0, baseline, title.upper())

        if note:
            c.setFont(FONT, 5.8)
            c.setFillColor(FAINT)
            c.drawRightString(PAGE_W - MARGIN_X, baseline + 0.6, truncate(note, 210, FONT, 5.8))

        c.setStrokeColor(RULE_SOFT)
        c.setLineWidth(0.5)
        c.line(MARGIN_X, baseline - 4.2, PAGE_W - MARGIN_X, baseline - 4.2)

    def _text_block(
        self,
        lines: List[str],
        x: float,
        top: float,
        size: float,
        font: str,
        color,
        leading: float,
    ) -> None:
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        for i, line in enumerate(lines):
            self.c.drawString(x, top - size - i * leading, line)

    # -- sections ----------------------------------------------------------

    def header(self) -> None:
        a = self.a
        c = self.c
        h = 44.0
        top = self.require(h)

        c.setFont(FONT_B, 7.0)
        c.setFillColor(ACCENT)
        c.drawString(MARGIN_X, top - 7.0, REPORT_TITLE.upper())

        name = a.profile.display_name()
        size = 17.0
        while text_width(name, FONT_B, size) > CONTENT_W - 150 and size > 10.5:
            size -= 0.5
        c.setFont(FONT_B, size)
        c.setFillColor(INK)
        c.drawString(MARGIN_X, top - 25.0, truncate(name, CONTENT_W - 150, FONT_B, size))

        # Meta line: sector | stage | date
        if self.template:
            stamp = "[DATE]"
        else:
            try:
                stamp = datetime.fromisoformat(a.analysis_timestamp).strftime("%d %b %Y")
            except ValueError:
                stamp = datetime.now().strftime("%d %b %Y")
        meta_parts = [
            a.profile.sector.display(""),
            a.profile.subsector.display(""),
            a.profile.funding_stage.display("") or a.profile.current_round.display(""),
            stamp,
        ]
        meta = "  |  ".join(p for p in meta_parts if p)
        c.setFont(FONT, 6.6)
        c.setFillColor(MUTED)
        c.drawString(MARGIN_X, top - 35.0, truncate(meta, CONTENT_W - 150, FONT, 6.6))

        # Right: research provenance, so the reader knows the evidence base.
        badge = self._provenance_badge()
        c.setFont(FONT, 5.6)
        c.setFillColor(FAINT)
        for i, line in enumerate(badge):
            c.drawRightString(PAGE_W - MARGIN_X, top - 8.0 - i * 7.0, line)

        c.setStrokeColor(ACCENT)
        c.setLineWidth(1.1)
        c.line(MARGIN_X, top - 40.0, PAGE_W - MARGIN_X, top - 40.0)

        # The brand's tri-colour signature, echoing the interface. Three solid
        # segments rather than a gradient: at 1.6pt a print gradient bands.
        seg = CONTENT_W / 3.0
        for i, colour in enumerate(BRAND_RULE):
            c.setStrokeColor(colour)
            c.setLineWidth(0.9)
            c.line(MARGIN_X + i * seg, top - 42.0, MARGIN_X + (i + 1) * seg, top - 42.0)

    def _provenance_badge(self) -> List[str]:
        if self.template:
            return ["[N] competitors assessed", "[EVIDENCE BASE]"]
        d = self.a.diagnostics
        lines = []
        lines.append(f"{len(self.a.competitors)} competitors assessed")
        if d.research_performed:
            lines.append("Deck + public sources")
        elif d.llm_available:
            lines.append("Deck + market knowledge")
        else:
            lines.append("Deck only — reduced confidence")
        if d.degraded:
            lines.append("DEGRADED RUN — see notes")
        return lines[:3]

    def section_position(self) -> None:
        a = self.a
        c = self.c
        self.section_header(1, "Competitive position")

        score = a.score
        left_w = 132.0
        right_x = MARGIN_X + left_w + 14.0
        right_w = CONTENT_W - left_w - 14.0

        # Height: the taller of the two columns.
        assessment = sentence_clip(
            a.assessment or score.explanation, self.b.assessment_chars
        )
        body = self.b.body_size
        assess_lines = wrap_limited(assessment, right_w, 5, FONT, body)
        pos_text = a.positioning.recommended or "Positioning not established."
        pos_lines = wrap_limited(pos_text, right_w - 12.0, 3, FONT_O, body)

        right_h = len(assess_lines) * (body + 2.0) + 6.0 + len(pos_lines) * (body + 2.0) + 13.0
        left_h = 62.0 if self.b.show_score_breakdown else 50.0
        h = max(right_h, left_h)
        top = self.require(h)

        # --- left: the score -------------------------------------------------
        numeral = "NN" if self.template else str(score.total)
        c.setFont(FONT_B, 27.0)
        c.setFillColor(FAINT if self.template else score_color(score.total))
        c.drawString(MARGIN_X, top - 23.0, numeral)
        c.setFont(FONT, 8.4)
        c.setFillColor(FAINT)
        c.drawString(MARGIN_X + text_width(numeral, FONT_B, 27.0) + 2.0, top - 23.0, "/100")

        c.setFont(FONT_B, 6.6)
        c.setFillColor(INK)
        c.drawString(MARGIN_X, top - 32.0, truncate(score.band, left_w, FONT_B, 6.6))

        confidence = "0.NN" if self.template else f"{score.confidence:.2f}"
        c.setFont(FONT, 5.8)
        c.setFillColor(MUTED)
        c.drawString(MARGIN_X, top - 40.0, f"Differentiation  ·  confidence {confidence}")

        bar_y = top - 52.0
        draw_score_bar(c, MARGIN_X, bar_y, left_w, score.total, template=self.template)

        if self.b.show_score_breakdown and score.categories:
            draw_category_strip(c, MARGIN_X, top - h + 1.0, left_w, score.categories[:4])

        # --- right: assessment + positioning ---------------------------------
        self._text_block(assess_lines, right_x, top, body, FONT, INK_SOFT, body + 2.0)
        py = top - len(assess_lines) * (body + 2.0) - 5.0

        box_h = len(pos_lines) * (body + 2.0) + 9.0
        c.setFillColor(ACCENT_SOFT)
        c.rect(right_x, py - box_h, right_w, box_h, stroke=0, fill=1)
        c.setStrokeColor(ACCENT)
        c.setLineWidth(1.4)
        c.line(right_x, py - box_h, right_x, py)

        c.setFont(FONT_B, 5.4)
        c.setFillColor(ACCENT)
        c.drawString(right_x + 6.0, py - 7.0, "RECOMMENDED POSITIONING")
        self._text_block(
            pos_lines, right_x + 6.0, py - 8.6, body, FONT_O, INK, body + 2.0
        )

    def section_matrix(self) -> None:
        a = self.a
        c = self.c
        rows = a.matrix.rows[: self.b.matrix_rows + 1]
        if not rows:
            return

        note = a.matrix.note or "Subject company shown first; competitors ranked by threat."
        self.section_header(2, "Competitive matrix", note)

        head_h = 10.0
        cell_size = 5.9
        lead = cell_size + 1.5

        # Pre-compute wrapped cells so row heights are known before drawing.
        prepared: List[Tuple[MatrixRow, List[List[str]], float]] = []
        for row in rows:
            values = [
                row.company, row.customer, row.product, row.pricing,
                row.traction, row.distribution, row.advantage, row.threat,
            ]
            cells: List[List[str]] = []
            for (_, width), value in zip(_MATRIX_COLS, values):
                cells.append(
                    wrap_limited(_compact(value), width - 6.0, 2, FONT, cell_size)
                )
            row_h = max(len(cell) for cell in cells) * lead + 4.6
            prepared.append((row, cells, row_h))

        total_h = head_h + sum(r[2] for r in prepared)
        top = self.require(total_h)

        # Header row.
        c.setFillColor(BAND)
        c.rect(MARGIN_X, top - head_h, CONTENT_W, head_h, stroke=0, fill=1)
        cx = MARGIN_X
        c.setFont(FONT_B, 5.5)
        c.setFillColor(INK_SOFT)
        for label, width in _MATRIX_COLS:
            c.drawString(cx + 3.0, top - head_h + 3.2, truncate(label, width - 5.0, FONT_B, 5.5))
            cx += width

        ry = top - head_h
        for row, cells, row_h in prepared:
            if row.is_subject:
                c.setFillColor(ACCENT_SOFT)
                c.rect(MARGIN_X, ry - row_h, CONTENT_W, row_h, stroke=0, fill=1)

            cx = MARGIN_X
            for i, ((_, width), lines) in enumerate(zip(_MATRIX_COLS, cells)):
                is_name = i == 0
                is_threat = i == len(_MATRIX_COLS) - 1
                font = FONT_B if (is_name and row.is_subject) else FONT
                color = INK if is_name else INK_SOFT
                if is_threat:
                    color = THREAT_COLOR.get(row.threat.strip().lower(), MUTED)
                    font = FONT_B
                if not lines:
                    cx += width
                    continue
                c.setFont(font, cell_size)
                c.setFillColor(color)
                for j, line in enumerate(lines):
                    c.drawString(cx + 3.0, ry - 3.2 - cell_size - j * lead, line)
                cx += width

            c.setStrokeColor(RULE_SOFT)
            c.setLineWidth(0.4)
            c.line(MARGIN_X, ry - row_h, PAGE_W - MARGIN_X, ry - row_h)
            ry -= row_h

    def section_map_and_whitespace(self) -> None:
        a = self.a
        show_map = self.b.show_market_map and a.market_map is not None
        items = a.whitespace[: self.b.whitespace_items]
        if not show_map and not items:
            return

        map_w = 236.0 if show_map else 0.0
        ws_x = MARGIN_X + (map_w + 14.0 if show_map else 0.0)
        ws_w = CONTENT_W - (map_w + 14.0 if show_map else 0.0)

        body = self.b.body_size - 0.5
        lead = body + 1.6

        # The difficulty tag sits in its own right-hand gutter so it can never
        # collide with the opportunity text.
        tag_w = 46.0
        text_x = ws_x + 8.0
        text_w = ws_w - 8.0 - tag_w

        # Whitespace block height.
        ws_h = 0.0
        prepared = []
        for item in items:
            title_lines = wrap_limited(item.opportunity, text_w, 2, FONT_B, body)
            support = " ".join(
                x for x in [item.investor_relevance or item.rationale, item.evidence] if x
            )
            support_lines = wrap_limited(
                support, text_w, self.b.whitespace_lines, FONT, body - 0.3
            )
            item_h = len(title_lines) * lead + len(support_lines) * (lead - 0.4) + 4.0
            prepared.append((item, title_lines, support_lines, item_h))
            ws_h += item_h

        h = max(self.b.map_height if show_map else 0.0, ws_h)

        titles = []
        if show_map:
            titles.append("Market map")
        if items:
            titles.append("Whitespace")
        self.section_header(3, " & ".join(titles), a.market_map.rationale if show_map else "")

        top = self.require(h)

        if show_map:
            draw_market_map(self.c, MARGIN_X, top - h, map_w, h, a.market_map)

        iy = top
        for item, title_lines, support_lines, item_h in prepared:
            c = self.c
            c.setFillColor(ACCENT)
            c.circle(ws_x + 2.6, iy - 4.2, 2.0, stroke=0, fill=1)

            self._text_block(title_lines, text_x, iy, body, FONT_B, INK, lead)
            sy = iy - len(title_lines) * lead
            self._text_block(
                support_lines, text_x, sy + 0.6, body - 0.3, FONT, MUTED, lead - 0.4
            )

            # Difficulty: three pips in the right gutter, filled by ease.
            filled = {"low": 3, "medium": 2, "high": 1}.get(item.difficulty, 2)
            px = PAGE_W - MARGIN_X - 3.0
            for k in range(3):
                c.setFillColor(ACCENT if k < filled else RULE)
                c.circle(px - k * 5.2, iy - 4.4, 1.7, stroke=0, fill=1)
            c.setFont(FONT, 4.4)
            c.setFillColor(FAINT)
            c.drawRightString(PAGE_W - MARGIN_X, iy - 11.4, item.difficulty.upper())
            iy -= item_h

    def section_watchlist(self) -> None:
        a = self.a
        c = self.c
        concerns = a.concerns[: self.b.concern_items]
        if not concerns:
            return

        self.section_header(
            4, "Investor watchlist", "Risk today  ·  question to ask  ·  evidence that settles it"
        )

        body = self.b.body_size - 0.4
        lead = body + 1.5
        label_w = 26.0

        prepared = []
        total = 0.0
        for concern in concerns:
            title = wrap_limited(concern.concern, CONTENT_W - 60.0, 1, FONT_B, body + 0.3)
            detail_lines: List[Tuple[str, str]] = []
            packed = [
                ("RISK", concern.current_risk),
                ("ASK", concern.diligence_question),
                ("NEED", concern.evidence_needed),
            ][: self.b.concern_lines]
            for label, text in packed:
                for k, line in enumerate(
                    wrap_limited(text, CONTENT_W - label_w - 12.0, 1, FONT, body)
                ):
                    detail_lines.append((label if k == 0 else "", line))
            item_h = (len(title) * (lead + 0.4)) + len(detail_lines) * lead + 3.4
            prepared.append((concern, title, detail_lines, item_h))
            total += item_h

        top = self.require(total)

        iy = top
        for concern, title, detail_lines, item_h in prepared:
            color = SEVERITY_COLOR.get(concern.severity, MUTED)
            # Severity bar.
            c.setFillColor(color)
            c.rect(MARGIN_X, iy - item_h + 2.6, 1.8, item_h - 3.4, stroke=0, fill=1)

            c.setFont(FONT_B, body + 0.3)
            c.setFillColor(INK)
            for i, line in enumerate(title):
                c.drawString(MARGIN_X + 6.0, iy - body - 0.3 - i * (lead + 0.4), line)

            c.setFont(FONT_B, 4.6)
            c.setFillColor(color)
            c.drawRightString(PAGE_W - MARGIN_X, iy - body - 0.3, concern.severity.upper())

            dy = iy - len(title) * (lead + 0.4)
            for label, line in detail_lines:
                if label:
                    c.setFont(FONT_B, 4.6)
                    c.setFillColor(FAINT)
                    c.drawString(MARGIN_X + 6.0, dy - body, label)
                c.setFont(FONT, body)
                c.setFillColor(INK_SOFT)
                c.drawString(MARGIN_X + 6.0 + label_w, dy - body, line)
                dy -= lead
            iy -= item_h

    def section_claims(self) -> None:
        """A single line naming claims to drop -- the most actionable output."""
        claims = self.a.positioning.claims_to_avoid
        if not self.b.show_claims_to_avoid or not claims:
            return
        body = self.b.body_size - 0.6
        text = "Claims to avoid: " + "  ·  ".join(_compact(x) for x in claims[:2])
        lines = wrap_limited(text, CONTENT_W, 2, FONT, body)
        if not lines:
            return
        h = len(lines) * (body + 1.6) + 4.0
        if self.remaining < h + 4.0:
            return  # optional content: skip rather than overflow
        top = self.require(h)
        self._text_block(lines, MARGIN_X, top, body, FONT, MUTED, body + 1.6)

    def footer(self) -> None:
        c = self.c
        y = 20.0
        c.setStrokeColor(RULE)
        c.setLineWidth(0.5)
        c.line(MARGIN_X, y + 9.0, PAGE_W - MARGIN_X, y + 9.0)

        c.setFont(FONT_B, 5.8)
        c.setFillColor(ACCENT)
        c.drawString(MARGIN_X, y, FOOTER_LEFT)

        c.setFont(FONT, 5.4)
        c.setFillColor(FAINT)
        centre = self._source_legend()
        c.drawCentredString(PAGE_W / 2.0, y, centre)

        c.setFont(FONT_O, 5.8)
        c.setFillColor(MUTED)
        c.drawRightString(PAGE_W - MARGIN_X, y, FOOTER_RIGHT)

    def _source_legend(self) -> str:
        """Compact source-indicator key (spec section 15)."""
        return "DECK pitch deck · DOC uploaded · WEB company site · PUB public source · INF analyst inference"

    # -- driver ------------------------------------------------------------

    def render(self) -> bytes:
        self.header()
        self.gap(6.0)
        self.section_position()
        self.gap()
        self.section_matrix()
        self.gap()
        self.section_map_and_whitespace()
        self.gap()
        self.section_watchlist()
        self.section_claims()
        self.footer()
        # Exactly one showPage, so the file cannot contain a second page.
        self.c.showPage()
        self.c.save()
        return self.buffer.getvalue()


#: Render-time defence against internal vocabulary reaching the page. The
#: enrichment stage strips this too; doing it here as well means no future
#: stage can leak it onto an investor-facing document.
_JARGON_RE = re.compile(
    r"\b(company[_ ]claim|stated[_ ]fact|analyst[_ ]inference|evidence[_ ]type)\b\s*[:\-—]?\s*",
    re.IGNORECASE,
)


def _compact(text: Optional[str]) -> str:
    """Normalise whitespace and drop sentinel noise from a display cell."""
    text = " ".join(str(text or "").split())
    if text in {UNKNOWN, NOT_DISCLOSED}:
        return "—" if text == UNKNOWN else "n/d"
    text = _JARGON_RE.sub("", text).strip(" :—-")
    # Values quoted by an upstream stage can end up double-quoted once wrapped
    # again for display; collapse that rather than showing '""claim"'.
    while len(text) > 1 and text[0] == text[1] == '"':
        text = text[1:]
    return text


# --------------------------------------------------------------------------
# Matrix construction (spec section 8)
# --------------------------------------------------------------------------


def build_matrix(analysis: CompetitiveAnalysis, rows: int, template: bool = False) -> None:
    """Populate `analysis.matrix` with the subject plus the top competitors."""
    from models.schemas import CompetitiveMatrix

    profile = analysis.profile
    subject = MatrixRow(
        company=profile.display_name(),
        customer=profile.target_customer.display(UNKNOWN),
        product=profile.core_product.display(UNKNOWN),
        pricing=profile.pricing_model.display(NOT_DISCLOSED),
        traction=profile.traction.display(profile.customers.display(NOT_DISCLOSED)),
        distribution=profile.distribution_model.display(UNKNOWN),
        advantage=(
            "[PRIMARY ADVANTAGE — STRONGEST EVIDENCED]"
            if template
            else _subject_advantage(analysis)
        ),
        threat="—",
        is_subject=True,
    )

    matrix_rows = [subject]
    for c in analysis.selected_competitors(rows):
        matrix_rows.append(
            MatrixRow(
                company=c.name,
                customer=c.target_customer,
                product=c.core_product,
                pricing=c.pricing,
                traction=_competitor_traction(c),
                distribution=c.distribution,
                advantage=(c.strengths[0] if c.strengths else c.defensibility),
                threat=c.threat_level.value,
            )
        )

    note = (
        "[N] competitors assessed; top [M] shown"
        if template
        else f"{len(analysis.competitors)} competitors assessed; top {len(matrix_rows) - 1} shown"
    )
    analysis.matrix = CompetitiveMatrix(rows=matrix_rows, note=note)


def _subject_advantage(analysis: CompetitiveAnalysis) -> str:
    """The company's strongest *evidenced* advantage, not its loudest claim."""
    from models.schemas import Advantage, EvidenceType

    ranked = sorted(
        (d for d in analysis.dimensions if d.advantage is Advantage.COMPANY),
        key=lambda d: (
            d.evidence_type is EvidenceType.STATED_FACT,
            {"high": 3, "medium": 2, "low": 1}.get(d.sustainability, 0),
            d.confidence,
        ),
        reverse=True,
    )
    if ranked:
        best = ranked[0]
        position = best.company_position
        if position and position != UNKNOWN:
            return f"{best.dimension}: {position}"
        return best.dimension
    return "No evidenced advantage established"


def _competitor_traction(c: Competitor) -> str:
    for value in (c.traction, c.customer_count, c.revenue, c.funding_raised):
        if value and value not in {UNKNOWN, NOT_DISCLOSED}:
            return value
    return NOT_DISCLOSED


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def generate_pdf(
    analysis: CompetitiveAnalysis,
    output_path: str | Path,
    settings: Optional[Settings] = None,
    template: bool = False,
) -> Path:
    """Render the one-page report, reducing content until it fits.

    Set `template=True` to render the blank structural skeleton through this
    same pipeline, so the template can never drift from the real output.

    Always writes a file. If even the most aggressive rung overflows, the last
    rung is rendered with clipping and the failure is recorded loudly in
    `analysis.diagnostics` rather than being hidden.
    """
    settings = settings or default_settings
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    diagnostics = analysis.diagnostics
    last_error: Optional[str] = None

    for index, budget in enumerate(LADDER[: settings.max_layout_passes]):
        build_matrix(analysis, budget.matrix_rows, template)
        try:
            data = _Renderer(analysis, budget, settings, template).render()
        except _Overflow as exc:
            last_error = str(exc)
            diagnostics.pdf_reduction_steps.append(f"'{budget.name}' overflowed: {exc}")
            continue

        output_path.write_bytes(data)
        pages = _count_pages(output_path)
        if pages != settings.max_pages:
            # Belt and braces: the geometry check said it fit, but the file
            # disagrees. Treat as an overflow and keep reducing.
            last_error = f"rendered {pages} pages at budget '{budget.name}'"
            diagnostics.pdf_reduction_steps.append(last_error)
            continue

        if index > 0:
            diagnostics.warn(
                f"Report content exceeded one page and was reduced to fit "
                f"(layout '{budget.name}', {index} reduction step(s)). "
                "Some competitors or supporting detail were omitted."
            )
        diagnostics.note(f"PDF rendered at layout budget '{budget.name}' ({pages} page).")
        logger.info("PDF written to %s at budget '%s'", output_path, budget.name)
        return output_path

    # Every rung overflowed. Render the tightest one anyway, clipped, and say so.
    budget = LADDER[-1]
    build_matrix(analysis, budget.matrix_rows, template)
    renderer = _Renderer(analysis, budget, settings, template)
    renderer.require = _clipping_require(renderer)  # type: ignore[method-assign]
    output_path.write_bytes(renderer.render())
    diagnostics.error(
        "The report could not be reduced to a single page by content trimming alone; "
        f"it was rendered clipped at the tightest layout ({last_error}). "
        "Shorten the analysis inputs or reduce CI_MATRIX_ROWS."
    )
    return output_path


def _clipping_require(renderer: "_Renderer"):
    """Replacement for `require` that clips instead of raising.

    Used only on the final fallback pass so the user still receives a file.
    """

    def require(height: float) -> float:
        top = renderer.y
        renderer.y -= height
        return top

    return require


def _count_pages(path: Path) -> int:
    """Verify the produced page count against the actual file."""
    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            return len(pdf.pages)
    except Exception:
        # Fall back to counting page objects in the raw PDF.
        try:
            return max(1, path.read_bytes().count(b"/Type /Page") - path.read_bytes().count(b"/Type /Pages"))
        except Exception:
            return 1
