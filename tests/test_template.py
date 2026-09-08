"""The document structure template.

Two properties matter and are asserted here:

  1. The template contains **no company-specific content** — only structure.
  2. The template is **structurally identical to a real report**, because it is
     rendered through the same pipeline. A test that pins this is what stops the
     template silently going stale when a section or column changes.
"""

from __future__ import annotations

import json
import re

import pdfplumber

from config import ALL_DIMENSIONS, SCORE_WEIGHTS
from models.schemas import PROFILE_FIELDS
from reporting.pdf_generator import generate_pdf
from reporting.template import (
    STRUCTURE,
    build_template_analysis,
    render_template_pdf,
    structure_markdown,
    template_record,
    write_all,
)


def page_text(path) -> str:
    with pdfplumber.open(str(path)) as pdf:
        return pdf.pages[0].extract_text() or ""


# --------------------------------------------------------------------------
# The template is blank
# --------------------------------------------------------------------------


def test_template_renders_exactly_one_page(tmp_path, settings):
    path = render_template_pdf(tmp_path / "template.pdf", settings)
    with pdfplumber.open(str(path)) as pdf:
        assert len(pdf.pages) == 1


def test_template_contains_no_company_specific_content(tmp_path, settings):
    """Nothing from the sample company, or any real company, may survive."""
    text = page_text(render_template_pdf(tmp_path / "template.pdf", settings))
    for token in (
        "Kestrel", "KestrelPA", "Availity", "Waystar", "Humata", "Infinx",
        "Cohere", "SamaCare", "Optum", "authorization", "specialty clinic",
        "Healthcare IT", "$840K", "142,000", "91%",
    ):
        assert token.lower() not in text.lower(), f"leaked company content: {token}"


def test_template_shows_no_real_score_or_date(tmp_path, settings):
    """A template must not read as a report about a company that scored zero."""
    text = page_text(render_template_pdf(tmp_path / "template.pdf", settings))
    # The numeral and its "/100" suffix are separate draw calls at different
    # x positions, so extraction may separate them; assert on each.
    assert "NN" in text
    assert "/100" in text
    assert "confidence 0.NN" in text
    assert "[DATE]" in text
    assert not re.search(r"\b20\d{2}\b", text), "a real year leaked into the template"


def test_every_visible_value_is_a_placeholder(tmp_path, settings):
    text = page_text(render_template_pdf(tmp_path / "template.pdf", settings))
    assert len(re.findall(r"\[[A-Z]", text)) > 60


def test_template_analysis_carries_no_real_confidence(settings):
    analysis = build_template_analysis(settings)
    assert analysis.score.total == 0
    assert analysis.score.confidence == 0.0
    assert all(c.confidence == 0.0 for c in analysis.competitors)
    assert all(f.confidence == 0.0 for f in (analysis.profile.get(n) for n in PROFILE_FIELDS))


# --------------------------------------------------------------------------
# The template matches the real document
# --------------------------------------------------------------------------


def test_template_has_the_same_sections_as_a_real_report(tmp_path, settings, analysis):
    """If a section is added to the report, the template gains it automatically."""
    template_text = page_text(render_template_pdf(tmp_path / "t.pdf", settings)).upper()
    report_text = page_text(generate_pdf(analysis, tmp_path / "r.pdf", settings)).upper()

    for heading in (
        "COMPETITIVE INTELLIGENCE",
        "COMPETITIVE POSITION",
        "COMPETITIVE MATRIX",
        "MARKET MAP",
        "WHITESPACE",
        "INVESTOR WATCHLIST",
        "RECOMMENDED POSITIONING",
        "TEN CAPITAL DECISION INTELLIGENCE",
        "CONFIDENTIAL",
    ):
        assert heading in template_text, f"template missing: {heading}"
        assert heading in report_text, f"report missing: {heading}"


def test_template_has_the_same_matrix_columns_as_a_real_report(tmp_path, settings):
    from reporting.pdf_generator import _MATRIX_COLS

    text = page_text(render_template_pdf(tmp_path / "t.pdf", settings))
    for label, _width in _MATRIX_COLS:
        assert label in text, f"template missing matrix column: {label}"


def test_template_covers_every_competitor_category(settings):
    """Each category must be visible so the structure teaches what belongs there."""
    analysis = build_template_analysis(settings)
    categories = {c.category.value for c in analysis.competitors}
    assert {"direct", "indirect", "incumbent", "emerging", "substitute"} <= categories


def test_template_shows_all_three_watchlist_fields(tmp_path, settings):
    """Risk / question / evidence must stay visibly distinct in the structure."""
    text = page_text(render_template_pdf(tmp_path / "t.pdf", settings))
    assert "CURRENT RISK" in text
    assert "DILIGENCE QUESTION" in text
    assert "EVIDENCE NEEDED" in text


# --------------------------------------------------------------------------
# The JSON skeleton
# --------------------------------------------------------------------------


def test_record_template_matches_the_real_record_shape(analysis):
    from reporting.record import build_record

    assert set(template_record()) == set(build_record(analysis))


def test_record_template_covers_every_profile_field():
    record = template_record()
    assert set(record["company_profile"]) == set(PROFILE_FIELDS)
    assert len(record["competitive_dimensions"]) == len(ALL_DIMENSIONS)
    assert len(record["analysis"]["category_scores"]) == len(SCORE_WEIGHTS)


def test_record_template_holds_no_real_values():
    record = template_record()
    blob = json.dumps(record)
    assert "Kestrel" not in blob
    assert record["analysis"]["differentiation_score"] == "[INTEGER 0-100]"
    assert record["consent"]["timestamp"] is None


def test_record_template_is_valid_json(tmp_path, settings):
    written = write_all(tmp_path, settings)
    loaded = json.loads(written["json"].read_text(encoding="utf-8"))
    assert loaded["schema_version"]


# --------------------------------------------------------------------------
# The written specification
# --------------------------------------------------------------------------


def test_structure_spec_documents_every_section():
    markdown = structure_markdown()
    for section in STRUCTURE["sections"]:
        assert section["name"] in markdown
    assert "Governing rules" in markdown
    assert "One-page reduction order" in markdown


def test_structure_spec_documents_every_matrix_column():
    from reporting.pdf_generator import _MATRIX_COLS

    matrix = next(s for s in STRUCTURE["sections"] if s["name"] == "Competitive matrix")
    documented = {c["field"] for c in matrix["columns"]}
    assert documented == {label for label, _ in _MATRIX_COLS}


def test_structure_spec_states_the_one_page_rule():
    assert "1" in STRUCTURE["document"]["page_count"]
    assert any("one page" in rule.lower() for rule in STRUCTURE["governing_rules"])


def test_write_all_emits_the_full_template_set(tmp_path, settings):
    written = write_all(tmp_path, settings)
    assert set(written) == {"pdf", "json", "markdown"}
    for path in written.values():
        assert path.exists() and path.stat().st_size > 0
