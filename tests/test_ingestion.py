"""Ingestion: PDF, PPTX, normalisation, and failure handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingestion.normalizer import build_transcript, ingest, read_document
from ingestion.pdf_reader import IngestionError, read_pdf
from ingestion.pptx_reader import read_pptx
from models.schemas import SourceType


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------


def test_pdf_extraction_reads_pages_and_text(sample_decks):
    doc = read_pdf(sample_decks["pdf"])
    assert doc.doc_type == "pdf"
    assert doc.unit_count == 12          # 11 content slides + the table page
    assert doc.char_count > 2000
    assert doc.source_type is SourceType.PITCH_DECK


def test_pdf_preserves_page_locators(sample_decks):
    doc = read_pdf(sample_decks["pdf"])
    locators = {b.locator for b in doc.blocks if b.text.strip()}
    assert "Page 1" in locators
    assert any(loc.startswith("Page ") for loc in locators)
    # Every text block must be traceable to a page.
    assert all(b.locator for b in doc.blocks if b.text.strip())


def test_pdf_first_line_becomes_title_block(sample_decks):
    doc = read_pdf(sample_decks["pdf"])
    titles = [b.text for b in doc.blocks if b.kind == "title"]
    assert any("Kestrel" in t for t in titles)


def test_pdf_missing_file_raises():
    with pytest.raises(IngestionError, match="File not found"):
        read_pdf("does_not_exist.pdf")


def test_pdf_corrupt_file_raises(tmp_path: Path):
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"%PDF-1.4 this is not actually a pdf at all \x00\x01\x02")
    with pytest.raises(IngestionError):
        read_pdf(bad)


def test_pdf_empty_file_raises(tmp_path: Path):
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    with pytest.raises(IngestionError, match="empty"):
        read_pdf(empty)


# --------------------------------------------------------------------------
# PPTX
# --------------------------------------------------------------------------


def test_pptx_extraction_reads_slides(sample_decks):
    doc = read_pptx(sample_decks["pptx"])
    assert doc.doc_type == "pptx"
    assert doc.unit_count == 12
    assert doc.char_count > 3000


def test_pptx_extracts_speaker_notes(sample_decks):
    """Speaker notes carry the candid version of a claim; losing them is a defect."""
    doc = read_pptx(sample_decks["pptx"])
    notes = [b for b in doc.blocks if b.kind == "notes"]
    assert len(notes) >= 8
    assert any("thin" in b.text.lower() for b in notes)


def test_pptx_extracts_tables(sample_decks):
    doc = read_pptx(sample_decks["pptx"])
    tables = [b for b in doc.blocks if b.kind == "table"]
    assert tables
    assert "Gross margin" in " ".join(t.text for t in tables)
    assert "|" in tables[0].text          # pipe-delimited rendering


def test_pptx_preserves_slide_locators(sample_decks):
    doc = read_pptx(sample_decks["pptx"])
    assert {"Slide 1", "Slide 2"} <= {b.locator for b in doc.blocks}


def test_pptx_corrupt_file_raises(tmp_path: Path):
    bad = tmp_path / "corrupt.pptx"
    bad.write_bytes(b"PK\x03\x04 not really a pptx")
    with pytest.raises(IngestionError):
        read_pptx(bad)


# --------------------------------------------------------------------------
# Dispatch and unsupported types
# --------------------------------------------------------------------------


def test_legacy_ppt_gives_actionable_error(tmp_path: Path):
    legacy = tmp_path / "deck.ppt"
    legacy.write_bytes(b"legacy")
    with pytest.raises(IngestionError, match="save as .pptx"):
        read_document(legacy)


def test_unsupported_type_gives_actionable_error(tmp_path: Path):
    odd = tmp_path / "deck.docx"
    odd.write_bytes(b"x")
    with pytest.raises(IngestionError, match="Unsupported file type"):
        read_document(odd)


def test_ingest_rejects_non_deck_extension(tmp_path: Path):
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")
    result = ingest(txt)
    assert not result.ok
    assert any("PDF or PPTX" in e for e in result.errors)


def test_ingest_without_deck_reports_error():
    result = ingest(None)
    assert not result.ok
    assert any("required" in e.lower() for e in result.errors)


def test_ingest_bad_supporting_document_degrades_not_fails(sample_decks, tmp_path: Path):
    """A broken supporting file must not take down a good run."""
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    result = ingest(sample_decks["pptx"], supporting_paths=[bad])
    assert result.ok                                  # the deck still read fine
    assert result.supporting == []
    assert any("Skipped supporting document" in w for w in result.warnings)


# --------------------------------------------------------------------------
# Image-only decks
# --------------------------------------------------------------------------


def test_image_only_pdf_warns_rather_than_silently_succeeding(tmp_path: Path):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas as pdfcanvas

    path = tmp_path / "images.pdf"
    c = pdfcanvas.Canvas(str(path), pagesize=letter)
    for _ in range(4):
        c.rect(100, 100, 200, 200, fill=1)   # shapes only, no text
        c.showPage()
    c.save()

    result = ingest(path)
    assert result.primary is not None
    assert result.primary.char_count == 0
    assert any("image-only" in e or "no extractable text" in e for e in result.errors)


# --------------------------------------------------------------------------
# Transcript
# --------------------------------------------------------------------------


def test_transcript_annotates_locators(sample_decks):
    result = ingest(sample_decks["pptx"])
    transcript, notes = build_transcript(result, 200_000)
    assert "--- Slide 1 ---" in transcript
    assert "[notes]" in transcript
    assert "[table]" in transcript
    assert notes == []


def test_transcript_truncation_is_reported(sample_decks):
    """Silent truncation would be a quality loss nobody could see."""
    result = ingest(sample_decks["pptx"])
    transcript, notes = build_transcript(result, 500)
    assert len(transcript) < 700
    assert notes and "truncated" in notes[0]


def test_transcript_includes_supporting_documents(sample_decks, tmp_path: Path):
    extra = tmp_path / "research.txt"
    extra.write_text("Market note: payer FHIR endpoints go live in 2027.", encoding="utf-8")
    result = ingest(sample_decks["pptx"], supporting_paths=[extra])
    transcript, _ = build_transcript(result, 200_000)
    assert "SUPPORTING DOCUMENT" in transcript
    assert "FHIR endpoints" in transcript
