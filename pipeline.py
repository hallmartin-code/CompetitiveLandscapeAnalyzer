"""
Pipeline orchestration: ingestion -> extraction -> research -> analysis -> output.

ARCHITECTURAL DECISION -- one orchestrator, two front ends
-----------------------------------------------------------
The CLI (`cli.py`) and the Streamlit app (`app.py`) are both thin shells over
`run_analysis`. Nothing analytical lives in a UI layer, so the two front ends
cannot drift apart, and the whole pipeline is testable without either.

The run is staged and interruptible: `analyse` stops after the analysis, so the
Streamlit app can show the competitor set and let the analyst edit it before
`render` produces the deliverables. `run_analysis` is the one-shot convenience
wrapper the CLI uses.

Every stage is expected to degrade rather than raise. Errors land in
`analysis.diagnostics` and are surfaced by both front ends.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

from analysis import competitor_classifier, differentiation, investor_risks
from analysis import market_map as market_map_module
from analysis import positioning as positioning_module
from analysis import whitespace as whitespace_module
from config import Settings, settings as default_settings
from extraction.company_extractor import build_profile
from ingestion.normalizer import build_transcript, ingest
from llm.client import LLMClient
from models.schemas import (
    CompetitiveAnalysis,
    Consent,
    RunDiagnostics,
    UserInputs,
)
from reporting.pdf_generator import generate_pdf
from reporting.record import write_record
from research.competitor_discovery import discover
from research.competitor_enrichment import enrich
from research.source_manager import SourceManager

logger = logging.getLogger(__name__)

#: Called with (step_index, total_steps, label) so a UI can show progress.
ProgressFn = Callable[[int, int, str], None]

_STEPS = [
    "Reading pitch deck",
    "Extracting company profile",
    "Discovering competitors",
    "Gathering competitor detail",
    "Assessing competitive dimensions",
    "Scoring differentiation",
    "Building market map",
    "Finding whitespace",
    "Identifying investor concerns",
    "Recommending positioning",
]


def _noop(_i: int, _n: int, _label: str) -> None:
    return None


def analyse(
    deck_path: str | Path,
    user: Optional[UserInputs] = None,
    consent: Optional[Consent] = None,
    supporting_paths: Optional[Iterable[str | Path]] = None,
    settings: Optional[Settings] = None,
    progress: Optional[ProgressFn] = None,
    client: Optional[LLMClient] = None,
) -> CompetitiveAnalysis:
    """Run every analytical stage. Returns a populated `CompetitiveAnalysis`.

    Never raises for content problems. A fatal ingestion failure returns an
    analysis whose `diagnostics.errors` explains what went wrong, so the caller
    can show the user a real message instead of a stack trace.
    """
    settings = settings or default_settings
    user = user or UserInputs()
    consent = consent or Consent()
    progress = progress or _noop
    total = len(_STEPS)

    diagnostics = RunDiagnostics()
    client = client or LLMClient(settings)
    diagnostics.llm_available = client.available
    if not client.available:
        diagnostics.warn(client.unavailable_reason or "No language model configured.")

    analysis = CompetitiveAnalysis(user=user, consent=consent, diagnostics=diagnostics)

    # -- 1. ingestion ------------------------------------------------------
    progress(1, total, _STEPS[0])
    ingestion = ingest(deck_path, supporting_paths)
    for err in ingestion.errors:
        diagnostics.error(err)
    for warn in ingestion.warnings:
        diagnostics.warn(warn)

    if ingestion.primary is None:
        diagnostics.degraded = True
        return analysis  # nothing to analyse; the caller reports the errors

    sources = SourceManager(consent, diagnostics, settings)

    # User-supplied URLs are fetched here so their content joins the transcript.
    extra_documents = sources.fetch_urls(list(user.source_urls or []))
    ingestion.supporting.extend(extra_documents)

    transcript, notes = build_transcript(ingestion, settings.max_transcript_chars)
    for note in notes:
        diagnostics.warn(note)

    # -- 2. company profile -------------------------------------------------
    progress(2, total, _STEPS[1])
    analysis.profile = build_profile(ingestion, transcript, user, client, diagnostics, settings)

    # -- 3. competitor discovery -------------------------------------------
    progress(3, total, _STEPS[2])
    extra_context = "\n\n".join(d.as_text(4000) for d in extra_documents)
    competitors = discover(
        analysis.profile, client, sources, diagnostics, settings, extra_context=extra_context
    )
    competitors = competitor_classifier.select(competitors, diagnostics, settings)

    # -- 4. enrichment ------------------------------------------------------
    progress(4, total, _STEPS[3])
    competitors = enrich(competitors, analysis.profile, client, sources, diagnostics, settings)
    analysis.competitors = competitors

    # -- 5/6. dimensions and score -----------------------------------------
    progress(5, total, _STEPS[4])
    analysis.dimensions, assessment = differentiation.assess_dimensions(
        analysis.profile, competitors, client, diagnostics
    )
    progress(6, total, _STEPS[5])
    analysis.score = differentiation.compute_score(
        analysis.dimensions, analysis.profile, competitors, diagnostics, settings
    )
    analysis.assessment = assessment or analysis.score.explanation

    # -- 7. market map ------------------------------------------------------
    progress(7, total, _STEPS[6])
    analysis.market_map = market_map_module.build(
        analysis.profile, competitors, client, diagnostics
    )

    # -- 8. whitespace ------------------------------------------------------
    progress(8, total, _STEPS[7])
    analysis.whitespace = whitespace_module.analyse(
        analysis.profile, competitors, client, diagnostics, settings
    )

    # -- 9. investor concerns ----------------------------------------------
    progress(9, total, _STEPS[8])
    analysis.concerns = investor_risks.identify(
        analysis.profile, competitors, analysis.dimensions, analysis.score,
        client, diagnostics, settings,
    )

    # -- 10. positioning ----------------------------------------------------
    progress(10, total, _STEPS[9])
    analysis.positioning = positioning_module.recommend(
        analysis.profile, competitors, analysis.dimensions, analysis.score, client, diagnostics
    )

    diagnostics.note(sources.summary())
    if client.calls:
        diagnostics.note(client.usage_summary())
    if client.blocked_domains:
        diagnostics.note(
            "Approved domains skipped because they block the research crawler: "
            + ", ".join(client.blocked_domains)
        )

    return analysis


def render(
    analysis: CompetitiveAnalysis,
    output_dir: Optional[str | Path] = None,
    stem: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> Tuple[Path, Path]:
    """Write the PDF and the JSON record. Returns (pdf_path, json_path)."""
    settings = settings or default_settings
    output_dir = Path(output_dir or settings.output_dir)
    stem = stem or _safe_stem(analysis)

    pdf_path = generate_pdf(analysis, output_dir / f"{stem}.pdf", settings)
    json_path = write_record(analysis, output_dir / f"{stem}.json")
    return pdf_path, json_path


def deliver(
    analysis: CompetitiveAnalysis,
    pdf_path: Optional[Path] = None,
    json_path: Optional[Path] = None,
    settings: Optional[Settings] = None,
) -> Tuple[bool, str]:
    """Email the finished report, if delivery is configured.

    Called automatically by `run_analysis` and explicitly by the Streamlit app,
    so a completed analysis is delivered the same way from either front end.
    Delivery is best-effort by design: the return value reports what happened
    and the diagnostics record it, but a failure never affects the artefacts
    already written to disk.
    """
    settings = settings or default_settings
    if not settings.email_configured:
        return False, "Email delivery is not configured."

    from notifications.email_sender import send_report

    return send_report(analysis, pdf_path, json_path, settings, analysis.diagnostics)


def run_analysis(
    deck_path: str | Path,
    user: Optional[UserInputs] = None,
    consent: Optional[Consent] = None,
    supporting_paths: Optional[Iterable[str | Path]] = None,
    settings: Optional[Settings] = None,
    output_dir: Optional[str | Path] = None,
    progress: Optional[ProgressFn] = None,
    email: bool = True,
) -> Tuple[CompetitiveAnalysis, Optional[Path], Optional[Path]]:
    """One-shot: analyse, render, and deliver.

    Returns (analysis, pdf_path, json_path). Paths are None when ingestion
    failed outright and there is nothing to report -- in which case no email is
    sent either, since there is nothing to send.
    """
    analysis = analyse(deck_path, user, consent, supporting_paths, settings, progress)
    if analysis.profile.coverage == 0 and analysis.diagnostics.errors and not analysis.competitors:
        return analysis, None, None
    pdf_path, json_path = render(analysis, output_dir, settings=settings)
    if email:
        deliver(analysis, pdf_path, json_path, settings)
    return analysis, pdf_path, json_path


def _safe_stem(analysis: CompetitiveAnalysis) -> str:
    import re
    from datetime import datetime

    name = analysis.profile.company_name.display("company")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()[:48] or "company"
    return f"{slug}_competitive_intelligence_{datetime.now().strftime('%Y%m%d')}"
