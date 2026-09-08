"""PDF rendering, layout fitting, and one-page enforcement."""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from models.schemas import (
    NOT_DISCLOSED,
    UNKNOWN,
    Competitor,
    InvestorConcern,
    ThreatLevel,
    WhitespaceOpportunity,
)
from reporting.layout import (
    LADDER,
    sentence_clip,
    text_width,
    truncate,
    wrap,
    wrap_limited,
)
from reporting.pdf_generator import build_matrix, generate_pdf
from tests.conftest import make_competitor


def page_count(path: Path) -> int:
    with pdfplumber.open(str(path)) as pdf:
        return len(pdf.pages)


def page_text(path: Path) -> str:
    with pdfplumber.open(str(path)) as pdf:
        return pdf.pages[0].extract_text() or ""


# --------------------------------------------------------------------------
# Text fitting primitives
# --------------------------------------------------------------------------


def test_truncate_respects_the_pixel_budget():
    long = "a very long competitor description that will not fit in a narrow column"
    out = truncate(long, 60.0, size=6.0)
    assert text_width(out, size=6.0) <= 60.0
    assert out.endswith("…")


def test_truncate_leaves_short_text_alone():
    assert truncate("Availity", 200.0, size=6.0) == "Availity"


def test_wrap_never_exceeds_the_width():
    text = "Prior authorization automation for specialty clinics across ortho and GI"
    for line in wrap(text, 120.0, size=7.0):
        assert text_width(line, size=7.0) <= 120.0


def test_wrap_limited_caps_lines_and_marks_the_cut():
    lines = wrap_limited(" ".join(["word"] * 200), 100.0, 3, size=7.0)
    assert len(lines) == 3
    assert lines[-1].endswith("…")


def test_sentence_clip_prefers_a_sentence_boundary():
    text = "First sentence here. Second sentence follows on. Third one trails."
    # A boundary late enough in the budget is used, so the clip reads as prose.
    assert sentence_clip(text, 30) == "First sentence here."
    # Text that already fits is returned untouched.
    assert sentence_clip(text, 500) == text


def test_sentence_clip_falls_back_to_a_word_boundary():
    """An early sentence break would waste most of the budget, so it is not used."""
    text = "Short one. " + " ".join(["elaboration"] * 40)
    clipped = sentence_clip(text, 120)
    assert clipped.endswith("…")
    assert len(clipped) <= 121
    assert not clipped.endswith(" …")


# --------------------------------------------------------------------------
# The reduction ladder
# --------------------------------------------------------------------------


def test_ladder_reduces_monotonically():
    """Each rung must be no more generous than the one before it."""
    for prev, nxt in zip(LADDER, LADDER[1:]):
        assert nxt.matrix_rows <= prev.matrix_rows
        assert nxt.assessment_chars <= prev.assessment_chars
        assert nxt.whitespace_items <= prev.whitespace_items
        assert nxt.concern_items <= prev.concern_items
        assert nxt.body_size <= prev.body_size


def test_ladder_shortens_narrative_before_dropping_competitors():
    """Spec priority order: shorten prose first, cut competitors second."""
    full, prose = LADDER[0], LADDER[1]
    assert prose.assessment_chars < full.assessment_chars
    assert prose.matrix_rows == full.matrix_rows


def test_ladder_never_drops_below_four_competitors():
    """The spec's matrix floor is four competitors."""
    assert all(rung.matrix_rows >= 4 for rung in LADDER)


def test_ladder_keeps_the_watchlist_longest():
    """The investor watchlist is the highest-value section; it is cut last."""
    last = LADDER[-1]
    assert last.concern_items >= 3
    assert not last.show_market_map          # the map goes before the watchlist


# --------------------------------------------------------------------------
# Matrix construction
# --------------------------------------------------------------------------


def test_matrix_puts_the_subject_first(analysis):
    build_matrix(analysis, 6)
    assert analysis.matrix.rows[0].is_subject
    assert analysis.matrix.rows[0].threat == "—"
    assert not any(r.is_subject for r in analysis.matrix.rows[1:])


def test_matrix_respects_the_row_budget(analysis):
    build_matrix(analysis, 4)
    assert len(analysis.matrix.rows) == 5          # subject + 4 competitors


def test_matrix_orders_competitors_by_threat(analysis):
    build_matrix(analysis, 6)
    threats = [r.threat for r in analysis.matrix.rows[1:]]
    assert threats[0] == "high"
    assert threats == sorted(threats, key=lambda t: {"high": 0, "medium": 1, "low": 2}[t])


def test_matrix_shows_sentinels_rather_than_invented_data(analysis):
    """A competitor with no known data must not gain any."""
    blank = Competitor(name="Unknown Rival", threat_level=ThreatLevel.HIGH, relevance=0.99)
    analysis.competitors = [blank]
    build_matrix(analysis, 6)
    row = analysis.matrix.rows[1]
    assert row.pricing == NOT_DISCLOSED
    assert row.traction == NOT_DISCLOSED
    assert row.customer == UNKNOWN


def test_subject_advantage_prefers_evidenced_dimensions(analysis):
    from models.schemas import Advantage, DimensionAssessment, EvidenceType

    analysis.dimensions = [
        DimensionAssessment(
            dimension="Brand advantage", advantage=Advantage.COMPANY,
            company_position="Loudly claimed", evidence_type=EvidenceType.COMPANY_CLAIM,
            confidence=0.9, sustainability="low",
        ),
        DimensionAssessment(
            dimension="Proprietary data", advantage=Advantage.COMPANY,
            company_position="Accumulating rules corpus",
            evidence_type=EvidenceType.STATED_FACT, confidence=0.6, sustainability="high",
        ),
    ]
    build_matrix(analysis, 6)
    assert "Proprietary data" in analysis.matrix.rows[0].advantage


# --------------------------------------------------------------------------
# One-page enforcement -- the hard constraint
# --------------------------------------------------------------------------


def test_report_is_exactly_one_page(analysis, tmp_path, settings):
    path = generate_pdf(analysis, tmp_path / "report.pdf", settings)
    assert path.exists()
    assert page_count(path) == 1


def test_report_renders_at_the_full_budget_for_normal_content(analysis, tmp_path, settings):
    generate_pdf(analysis, tmp_path / "report.pdf", settings)
    assert any("'full'" in n for n in analysis.diagnostics.info)
    assert analysis.diagnostics.pdf_reduction_steps == []


def test_absurd_content_still_produces_one_page(analysis, tmp_path, settings):
    """The one-page rule holds even when every section is oversized."""
    filler = " ".join(["extraordinarily"] * 400)
    analysis.assessment = filler
    analysis.competitors = [
        make_competitor(
            f"Competitor With A Very Long Name Number {i}",
            threat=ThreatLevel.HIGH,
            core_product=filler,
            target_customer=filler,
            distribution=filler,
            strengths=[filler],
        )
        for i in range(14)
    ]
    analysis.whitespace = [
        WhitespaceOpportunity(
            opportunity=filler, evidence=filler, rationale=filler,
            difficulty="high", investor_relevance=filler,
        )
        for _ in range(8)
    ]
    analysis.concerns = [
        InvestorConcern(
            concern=filler, current_risk=filler,
            diligence_question=filler, evidence_needed=filler, severity="high",
        )
        for _ in range(9)
    ]

    path = generate_pdf(analysis, tmp_path / "huge.pdf", settings)
    assert page_count(path) == 1
    # Nothing is allowed to spill off the page edge either: the deepest section
    # must still sit above the bottom margin.
    with pdfplumber.open(str(path)) as pdf:
        page = pdf.pages[0]
        assert all(word["bottom"] <= page.height for word in page.extract_words())


def test_ladder_engages_when_the_page_budget_shrinks(analysis, tmp_path, settings, monkeypatch):
    """Force overflow by raising the floor, and prove the ladder recovers.

    Content is bounded at every rung by design, so a normal analysis fits at
    the full budget and the ladder never fires. Shrinking the usable page is
    the honest way to exercise the reduction path itself.
    """
    monkeypatch.setattr("reporting.pdf_generator.CONTENT_BOTTOM", 280.0)

    path = generate_pdf(analysis, tmp_path / "tight.pdf", settings)
    d = analysis.diagnostics

    assert page_count(path) == 1
    assert d.pdf_reduction_steps, "the ladder should have recorded its failed attempts"
    assert any("exceeded one page" in w for w in d.warnings), "the user must be told"
    # It settled on a later rung, not the full layout.
    assert not any("'full'" in n for n in d.info)


def test_reduction_drops_the_map_before_the_watchlist(analysis, tmp_path, settings, monkeypatch):
    """Investor-relevant content survives longest (spec priority 3)."""
    monkeypatch.setattr("reporting.pdf_generator.CONTENT_BOTTOM", 320.0)
    path = generate_pdf(analysis, tmp_path / "tight.pdf", settings)
    text = page_text(path).upper()
    assert "INVESTOR WATCHLIST" in text


def test_impossible_layout_still_delivers_a_file_and_reports_failure(
    analysis, tmp_path, settings, monkeypatch
):
    """Even when no rung fits, the user gets a file and a loud error."""
    monkeypatch.setattr("reporting.pdf_generator.CONTENT_BOTTOM", 430.0)

    path = generate_pdf(analysis, tmp_path / "impossible.pdf", settings)
    assert path.exists() and path.stat().st_size > 0
    assert any("could not be reduced to a single page" in e for e in analysis.diagnostics.errors)


def test_minimal_analysis_still_renders(analysis, tmp_path, settings):
    """A near-empty analysis must produce a valid page, not a crash."""
    analysis.competitors = []
    analysis.whitespace = []
    analysis.concerns = []
    analysis.market_map = None
    analysis.assessment = ""
    path = generate_pdf(analysis, tmp_path / "minimal.pdf", settings)
    assert page_count(path) == 1


# --------------------------------------------------------------------------
# Page content
# --------------------------------------------------------------------------


def test_page_carries_the_required_chrome(analysis, tmp_path, settings):
    path = generate_pdf(analysis, tmp_path / "report.pdf", settings)
    text = page_text(path)
    assert "COMPETITIVE INTELLIGENCE" in text
    assert "TEN Capital Decision Intelligence" in text
    assert "Confidential" in text and "for recipients only" in text


def test_page_carries_every_required_section(analysis, tmp_path, settings):
    path = generate_pdf(analysis, tmp_path / "report.pdf", settings)
    text = page_text(path).upper()
    for heading in (
        "COMPETITIVE POSITION",
        "COMPETITIVE MATRIX",
        "WHITESPACE",
        "INVESTOR WATCHLIST",
    ):
        assert heading in text, f"missing section: {heading}"


def test_page_shows_score_band_and_positioning(analysis, tmp_path, settings):
    path = generate_pdf(analysis, tmp_path / "report.pdf", settings)
    text = page_text(path)
    assert str(analysis.score.total) in text
    assert analysis.score.band in text
    assert "RECOMMENDED POSITIONING" in text
    assert f"confidence {analysis.score.confidence:.2f}" in text


def test_page_shows_a_source_legend(analysis, tmp_path, settings):
    """Spec section 15: compact source indicators must be decodable."""
    path = generate_pdf(analysis, tmp_path / "report.pdf", settings)
    text = page_text(path)
    assert "DECK" in text and "PUB" in text and "INF" in text


def test_internal_vocabulary_never_reaches_the_page(analysis, tmp_path, settings):
    """Pipeline jargon in a data field must be stripped before rendering."""
    analysis.competitors[0].traction = "company_claim (June 2024 release)"
    path = generate_pdf(analysis, tmp_path / "report.pdf", settings)
    assert "company_claim" not in page_text(path).lower()


def test_company_name_appears_in_the_header(analysis, tmp_path, settings):
    path = generate_pdf(analysis, tmp_path / "report.pdf", settings)
    assert "Kestrel" in page_text(path)


def test_output_directory_is_created(analysis, tmp_path, settings):
    nested = tmp_path / "a" / "b" / "c" / "report.pdf"
    generate_pdf(analysis, nested, settings)
    assert nested.exists()
