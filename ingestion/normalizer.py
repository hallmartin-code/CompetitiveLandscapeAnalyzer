"""
Ingestion front door.

Dispatches on file type, normalizes everything into `SourceDocument`, and
collects errors and warnings into a single `IngestionResult` so the caller can
decide whether to continue in a degraded mode rather than being handed an
exception or, worse, a silently empty document.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Optional

from models.schemas import DocumentBlock, IngestionResult, SourceDocument, SourceType

from .pdf_reader import IngestionError, read_pdf
from .pptx_reader import read_pptx

logger = logging.getLogger(__name__)

SUPPORTED_DECK_SUFFIXES = {".pdf", ".pptx"}
SUPPORTED_DOC_SUFFIXES = {".pdf", ".pptx", ".txt", ".md"}


def read_text_file(path: str | Path, source_type: SourceType = SourceType.UPLOADED_DOCUMENT) -> SourceDocument:
    """Read a plain-text or markdown supporting document."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise IngestionError(f"Could not read '{path.name}': {exc}") from exc

    doc = SourceDocument(
        name=path.name,
        path=str(path),
        doc_type="text",
        source_type=source_type,
        unit_count=1,
    )
    # Chunk on blank lines so long documents still yield usable locators.
    chunks = [c.strip() for c in raw.split("\n\n") if c.strip()]
    for i, chunk in enumerate(chunks, start=1):
        doc.blocks.append(
            DocumentBlock(kind="body", text=chunk, index=i, locator=f"{path.name} §{i}")
        )
    doc.unit_count = max(1, len(chunks))
    if not chunks:
        doc.warnings.append(f"'{path.name}' contained no readable text.")
    return doc


def read_document(path: str | Path, source_type: SourceType = SourceType.PITCH_DECK) -> SourceDocument:
    """Read any supported file into a `SourceDocument`.

    Raises:
        IngestionError: unsupported type, or the file could not be parsed.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return read_pdf(path, source_type=source_type)
    if suffix == ".pptx":
        return read_pptx(path, source_type=source_type)
    if suffix in {".txt", ".md"}:
        return read_text_file(path, source_type=source_type)
    if suffix == ".ppt":
        raise IngestionError(
            f"'{path.name}' is a legacy .ppt file, which cannot be read directly. "
            "Open it in PowerPoint and save as .pptx, then retry."
        )
    if suffix == ".key":
        raise IngestionError(
            f"'{path.name}' is a Keynote file. Export it to PDF or PPTX and retry."
        )
    raise IngestionError(
        f"Unsupported file type '{suffix or path.name}'. "
        f"Supported: {', '.join(sorted(SUPPORTED_DOC_SUFFIXES))}."
    )


def ingest(
    deck_path: Optional[str | Path],
    supporting_paths: Optional[Iterable[str | Path]] = None,
) -> IngestionResult:
    """Ingest a pitch deck plus any supporting documents.

    Never raises for content problems: a failure to read one supporting document
    degrades the run rather than ending it. A failure to read the *deck* is
    recorded as an error and leaves `result.ok` False.
    """
    result = IngestionResult()

    if deck_path is None:
        result.errors.append("No pitch deck supplied. A PDF or PPTX deck is required.")
        return result

    deck_path = Path(deck_path)
    if deck_path.suffix.lower() not in SUPPORTED_DECK_SUFFIXES:
        result.errors.append(
            f"Pitch deck must be a PDF or PPTX file; got '{deck_path.suffix or deck_path.name}'."
        )
        return result

    try:
        result.primary = read_document(deck_path, source_type=SourceType.PITCH_DECK)
        result.warnings.extend(result.primary.warnings)
    except IngestionError as exc:
        result.errors.append(str(exc))
        return result
    except Exception as exc:  # defensive: unexpected parser failure
        logger.exception("Unexpected error reading deck")
        result.errors.append(f"Unexpected error reading '{deck_path.name}': {exc}")
        return result

    if result.primary.char_count == 0:
        result.errors.append(
            f"'{deck_path.name}' was read successfully but contains no extractable text. "
            "Provide company details manually, or supply a text-based version of the deck."
        )

    for extra in supporting_paths or []:
        try:
            doc = read_document(extra, source_type=SourceType.UPLOADED_DOCUMENT)
            result.supporting.append(doc)
            result.warnings.extend(doc.warnings)
        except IngestionError as exc:
            result.warnings.append(f"Skipped supporting document: {exc}")
        except Exception as exc:
            logger.exception("Unexpected error reading supporting document")
            result.warnings.append(f"Skipped supporting document '{extra}': {exc}")

    return result


def build_transcript(result: IngestionResult, max_chars: int) -> tuple[str, List[str]]:
    """Flatten ingested documents into one locator-annotated transcript.

    Returns the transcript plus any notes about truncation, so a shortened input
    is always visible in diagnostics rather than being a silent quality loss.
    """
    notes: List[str] = []
    parts: List[str] = []

    if result.primary:
        parts.append(f"===== PITCH DECK: {result.primary.name} =====")
        parts.append(result.primary.as_text())

    for doc in result.supporting:
        parts.append(f"\n===== SUPPORTING DOCUMENT: {doc.name} =====")
        parts.append(doc.as_text())

    transcript = "\n".join(parts).strip()
    if len(transcript) > max_chars:
        transcript = transcript[:max_chars] + "\n[... transcript truncated at configured limit ...]"
        notes.append(
            f"Source material exceeded {max_chars:,} characters and was truncated; "
            "later pages of supporting documents were not analysed."
        )
    return transcript, notes
