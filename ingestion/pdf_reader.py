"""
PDF pitch deck reader.

Extracts text, tables and metadata page by page, preserving the page number so
downstream conclusions remain traceable to "Page N".

Failure handling is explicit: a corrupt file raises `IngestionError`, and a
readable-but-textless file (an image-only deck) returns a document carrying a
loud warning rather than an empty success. The distinction matters -- the first
is a broken input, the second is a real deck we simply cannot read, and the
report must say so.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from models.schemas import DocumentBlock, SourceDocument, SourceType

logger = logging.getLogger(__name__)


class IngestionError(Exception):
    """Raised when a file cannot be opened or parsed at all."""


#: Below this many characters per page we assume the deck is image-based and
#: warn that the analysis will be running on very little text.
MIN_CHARS_PER_PAGE = 40

#: Titles authoring tools stamp on files nobody named. Treated as absent:
#: reporting on a company called "untitled" is worse than reporting on one
#: whose name we admit we could not determine.
PLACEHOLDER_TITLES = {
    "untitled",
    "unspecified",
    "document",
    "presentation",
    "powerpoint presentation",
    "microsoft word",
    "slide 1",
    "no title",
}


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    lines = [ln.strip() for ln in text.replace("\x00", "").splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _table_to_text(table: List[List[Optional[str]]]) -> str:
    """Render an extracted table as pipe-delimited rows.

    Kept as text rather than a structured object because the consumer is a
    language model reading a transcript; a faithful textual rendering carries
    the same information with far less machinery.
    """
    rows: List[str] = []
    for row in table or []:
        cells = [(c or "").replace("\n", " ").strip() for c in row]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def read_pdf(path: str | Path, source_type: SourceType = SourceType.PITCH_DECK) -> SourceDocument:
    """Read a PDF into the common document model.

    Raises:
        IngestionError: file missing, not a PDF, or unparseable.
    """
    path = Path(path)
    if not path.exists():
        raise IngestionError(f"File not found: {path}")
    if path.stat().st_size == 0:
        raise IngestionError(f"File is empty: {path.name}")

    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise IngestionError("pdfplumber is not installed; run pip install -r requirements.txt") from exc

    doc = SourceDocument(
        name=path.name,
        path=str(path),
        doc_type="pdf",
        source_type=source_type,
    )

    try:
        with pdfplumber.open(str(path)) as pdf:
            doc.unit_count = len(pdf.pages)
            if pdf.metadata:
                # Metadata occasionally carries the real company name when the
                # deck's cover slide is an image. Placeholder titles are dropped
                # here, at the source, so no downstream consumer can mistake
                # "untitled" for a company name.
                doc.metadata = {
                    k: str(v)
                    for k, v in pdf.metadata.items()
                    if k in {"Title", "Author", "Subject", "Creator", "Producer"}
                    and v
                    and str(v).strip().lower() not in PLACEHOLDER_TITLES
                }

            for page_no, page in enumerate(pdf.pages, start=1):
                locator = f"Page {page_no}"
                try:
                    text = _clean(page.extract_text())
                except Exception as exc:  # a single bad page must not kill the run
                    doc.warnings.append(f"{locator}: text extraction failed ({exc.__class__.__name__})")
                    text = ""

                if text:
                    lines = text.split("\n")
                    # First line of a slide-style page is nearly always its title.
                    doc.blocks.append(
                        DocumentBlock(kind="title", text=lines[0], index=page_no, locator=locator)
                    )
                    body = "\n".join(lines[1:]).strip()
                    if body:
                        doc.blocks.append(
                            DocumentBlock(kind="body", text=body, index=page_no, locator=locator)
                        )

                try:
                    for table in page.extract_tables() or []:
                        rendered = _table_to_text(table)
                        if rendered:
                            doc.blocks.append(
                                DocumentBlock(
                                    kind="table", text=rendered, index=page_no, locator=locator
                                )
                            )
                except Exception as exc:
                    doc.warnings.append(f"{locator}: table extraction failed ({exc.__class__.__name__})")

    except IngestionError:
        raise
    except Exception as exc:
        raise IngestionError(
            f"Could not read PDF '{path.name}': {exc.__class__.__name__}: {exc}. "
            "The file may be corrupted, encrypted, or password-protected."
        ) from exc

    if doc.unit_count == 0:
        raise IngestionError(f"'{path.name}' contains no pages.")

    _add_coverage_warnings(doc)
    title = (doc.metadata.get("Title") or "").strip()
    if title:
        doc.blocks.append(
            DocumentBlock(
                kind="metadata",
                text=f"PDF document title: {title}",
                index=0,
                locator="File metadata",
            )
        )
    logger.info(
        "Read PDF %s: %d pages, %d chars, %d blocks",
        path.name,
        doc.unit_count,
        doc.char_count,
        len(doc.blocks),
    )
    return doc


def _add_coverage_warnings(doc: SourceDocument) -> None:
    """Attach warnings about thin or image-only content."""
    if doc.char_count == 0:
        doc.warnings.append(
            "No selectable text found. This looks like an image-only or scanned deck; "
            "run OCR, or supply company details manually, before relying on the analysis."
        )
        return

    avg = doc.char_count / max(1, doc.unit_count)
    if avg < MIN_CHARS_PER_PAGE:
        doc.warnings.append(
            f"Only {doc.char_count} characters across {doc.unit_count} pages "
            f"({avg:.0f}/page). The deck is largely graphical; extraction confidence is reduced."
        )
    empty = doc.unit_count - doc.text_bearing_units
    if empty > 0 and doc.unit_count:
        share = empty / doc.unit_count
        if share >= 0.4:
            doc.warnings.append(
                f"{empty} of {doc.unit_count} pages yielded no text (likely images)."
            )
