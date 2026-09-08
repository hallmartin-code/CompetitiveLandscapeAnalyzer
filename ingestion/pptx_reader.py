"""
PowerPoint pitch deck reader.

Extracts slide titles, body text, tables, grouped shapes and speaker notes,
preserving slide numbers as locators ("Slide 4").

Speaker notes are worth the extra code: founders routinely put the honest
version of a claim -- the caveat, the actual customer count, the competitor
they are worried about -- in the notes rather than on the slide.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List

from models.schemas import DocumentBlock, SourceDocument, SourceType

from .pdf_reader import IngestionError, MIN_CHARS_PER_PAGE

logger = logging.getLogger(__name__)


def _clean(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").replace("\x0b", "\n").splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _iter_shapes(shapes) -> Iterable:
    """Walk shapes recursively so text inside grouped shapes is not lost."""
    for shape in shapes:
        # 6 == MSO_SHAPE_TYPE.GROUP; compared numerically to avoid importing
        # the enum, which changes module path between python-pptx versions.
        if getattr(shape, "shape_type", None) == 6 and hasattr(shape, "shapes"):
            yield from _iter_shapes(shape.shapes)
        else:
            yield shape


def _table_to_text(table) -> str:
    rows: List[str] = []
    for row in table.rows:
        cells = [c.text.replace("\n", " ").strip() for c in row.cells]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def read_pptx(path: str | Path, source_type: SourceType = SourceType.PITCH_DECK) -> SourceDocument:
    """Read a .pptx into the common document model.

    Raises:
        IngestionError: file missing, not a valid PPTX, or unparseable.
    """
    path = Path(path)
    if not path.exists():
        raise IngestionError(f"File not found: {path}")
    if path.stat().st_size == 0:
        raise IngestionError(f"File is empty: {path.name}")

    try:
        from pptx import Presentation
    except ImportError as exc:  # pragma: no cover
        raise IngestionError("python-pptx is not installed; run pip install -r requirements.txt") from exc

    doc = SourceDocument(
        name=path.name,
        path=str(path),
        doc_type="pptx",
        source_type=source_type,
    )

    try:
        prs = Presentation(str(path))
    except Exception as exc:
        raise IngestionError(
            f"Could not read PowerPoint file '{path.name}': {exc.__class__.__name__}: {exc}. "
            "The file may be corrupted, or saved in the legacy .ppt format "
            "(re-save as .pptx and retry)."
        ) from exc

    try:
        core = prs.core_properties
        doc.metadata = {
            k: str(v)
            for k, v in {
                "Title": core.title,
                "Author": core.author,
                "Subject": core.subject,
                "Company": getattr(core, "category", None),
            }.items()
            if v
        }
    except Exception:  # metadata is a bonus, never a blocker
        doc.metadata = {}

    slides = list(prs.slides)
    doc.unit_count = len(slides)
    if doc.unit_count == 0:
        raise IngestionError(f"'{path.name}' contains no slides.")

    for slide_no, slide in enumerate(slides, start=1):
        locator = f"Slide {slide_no}"

        # Title placeholder, when the template provides one.
        title_text = ""
        try:
            if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
                title_text = _clean(slide.shapes.title.text)
        except Exception:
            title_text = ""
        if title_text:
            doc.blocks.append(
                DocumentBlock(kind="title", text=title_text, index=slide_no, locator=locator)
            )

        body_parts: List[str] = []
        try:
            for shape in _iter_shapes(slide.shapes):
                if getattr(shape, "has_table", False):
                    rendered = _table_to_text(shape.table)
                    if rendered:
                        doc.blocks.append(
                            DocumentBlock(
                                kind="table", text=rendered, index=slide_no, locator=locator
                            )
                        )
                    continue
                if getattr(shape, "has_text_frame", False):
                    text = _clean(shape.text_frame.text)
                    if text and text != title_text:
                        body_parts.append(text)
        except Exception as exc:
            doc.warnings.append(f"{locator}: shape extraction failed ({exc.__class__.__name__})")

        if body_parts:
            doc.blocks.append(
                DocumentBlock(
                    kind="body", text="\n".join(body_parts), index=slide_no, locator=locator
                )
            )

        # Speaker notes -- often the most candid content in the deck.
        try:
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
                notes = _clean(slide.notes_slide.notes_text_frame.text)
                if notes:
                    doc.blocks.append(
                        DocumentBlock(kind="notes", text=notes, index=slide_no, locator=locator)
                    )
        except Exception:
            pass

    _add_coverage_warnings(doc)
    logger.info(
        "Read PPTX %s: %d slides, %d chars, %d blocks",
        path.name,
        doc.unit_count,
        doc.char_count,
        len(doc.blocks),
    )
    return doc


def _add_coverage_warnings(doc: SourceDocument) -> None:
    if doc.char_count == 0:
        doc.warnings.append(
            "No text found in any slide. The deck appears to be built entirely from images; "
            "supply company details manually before relying on the analysis."
        )
        return
    avg = doc.char_count / max(1, doc.unit_count)
    if avg < MIN_CHARS_PER_PAGE:
        doc.warnings.append(
            f"Only {doc.char_count} characters across {doc.unit_count} slides "
            f"({avg:.0f}/slide). Extraction confidence is reduced."
        )
    empty = doc.unit_count - doc.text_bearing_units
    if empty and empty / doc.unit_count >= 0.4:
        doc.warnings.append(f"{empty} of {doc.unit_count} slides yielded no text (likely images).")
