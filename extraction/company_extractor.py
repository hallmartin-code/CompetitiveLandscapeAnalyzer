"""
Company profile extraction (spec section 3).

Two independent paths produce a `CompanyProfile`:

  1. `extract_with_llm`  -- schema-constrained model extraction with locators
  2. `extract_heuristic` -- deterministic keyword/regex extraction

The heuristic path is not a toy. It is the reason the application still returns
something honest when there is no API credential, when the model call fails, or
when a test needs a fast deterministic fixture. It is intentionally conservative:
it only claims things it can point at.

Both paths feed `merge_profiles`, and user-supplied form fields are layered on
top last because the operator outranks both.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from pydantic import BaseModel, Field

from config import Settings, settings as default_settings
from llm.client import LLMClient
from models.schemas import (
    PROFILE_FIELDS,
    CompanyProfile,
    IngestionResult,
    RunDiagnostics,
    SourceType,
    UserInputs,
    is_nullish,
)

from .evidence import grade_claim, make_fact, merge_fact

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# LLM-facing schema
# --------------------------------------------------------------------------


class ExtractedField(BaseModel):
    """One extracted profile field, with its provenance.

    Modelled as a *list of items* rather than a 34-property object so the model
    can simply omit what is not present, instead of being nudged into filling
    every slot with a plausible-looking value.
    """

    field: str = Field(description="Field name; must be one of the allowed field names.")
    value: str = Field(description="The extracted value, kept short and specific.")
    source: str = Field(
        description="Where it came from, e.g. 'Slide 4', 'Page 2', or 'analyst inference'."
    )
    evidence_type: str = Field(
        description="One of: stated_fact, company_claim, analyst_inference, unverified."
    )
    confidence: float = Field(description="0.0-1.0 calibrated confidence.")
    excerpt: str = Field(default="", description="Short supporting quote from the source.")


class CompanyExtraction(BaseModel):
    fields: List[ExtractedField] = Field(description="Only fields actually supported by the material.")
    notes: List[str] = Field(
        default_factory=list,
        description="Material ambiguities, conflicts or gaps an analyst should know about.",
    )


_FIELD_GUIDE = """\
ALLOWED FIELD NAMES (use these exact strings; omit any field the material does not support):
company_name, website, headquarters, sector, subsector, product_category, core_product,
problem_addressed, target_customer, buyer, end_user, primary_use_cases, business_model,
pricing_model, revenue, revenue_growth, customers, users, partnerships, distribution_model,
geographic_markets, funding_raised, current_round, valuation, key_ip, regulatory_advantages,
proprietary_data, network_effects, switching_costs, claimed_differentiation, funding_stage,
raise_amount, traction, known_competitors

FIELD NOTES
- buyer vs end_user vs target_customer: the buyer holds the budget, the end user
  operates the product, the target customer is the organisation being sold to.
  They are frequently different; say so when the deck conflates them.
- claimed_differentiation: capture the company's own words for why it wins, verbatim
  enough to be checkable. Mark it company_claim unless the deck cites a source.
- key_ip: state the actual status. A filed application is not a granted patent;
  if the deck says "patent pending", the value must say pending.
- known_competitors: only competitors the deck itself names.
- Omit a field entirely rather than guessing. Do not output "Unknown" as a value.
"""


def extract_with_llm(
    transcript: str,
    client: LLMClient,
    diagnostics: RunDiagnostics,
    user: Optional[UserInputs] = None,
) -> Optional[CompanyProfile]:
    """Model-backed extraction. Returns None (and records why) on failure."""
    context = ""
    if user and (user.notes or user.product_description):
        context = "\n\nANALYST-SUPPLIED CONTEXT (treat as user_provided, high trust):\n"
        if user.product_description:
            context += f"- Product description: {user.product_description}\n"
        if user.notes:
            context += f"- Notes: {user.notes}\n"

    prompt = (
        "Extract a structured company profile from the investor material below.\n\n"
        f"{_FIELD_GUIDE}\n"
        "Give every field a locator ('Slide N' / 'Page N') when it comes from the material. "
        "Use 'analyst inference' as the source only where you genuinely reasoned it out, "
        "and keep confidence below 0.6 when you do.\n\n"
        "In notes, record: conflicts between slides, claims presented as fact without a "
        "source, and any field an investor would expect that is simply absent."
        f"{context}\n\n"
        "===== INVESTOR MATERIAL =====\n"
        f"{transcript}\n"
        "===== END MATERIAL ====="
    )

    result, error = client.structured(
        prompt, CompanyExtraction, label="company-extraction", max_tokens=16000
    )
    if error or result is None:
        diagnostics.warn(f"Company extraction fell back to heuristics: {error}")
        return None

    profile = CompanyProfile()
    unknown_fields: List[str] = []
    for item in result.fields:
        name = item.field.strip().lower()
        if name not in PROFILE_FIELDS:
            unknown_fields.append(item.field)
            continue
        if is_nullish(item.value):
            continue
        fact = make_fact(
            value=item.value,
            locator=item.source,
            evidence_type=item.evidence_type,
            confidence=item.confidence,
            excerpt=item.excerpt,
        )
        setattr(profile, name, merge_fact(getattr(profile, name), fact))

    profile.extraction_notes = list(result.notes)
    if unknown_fields:
        diagnostics.note(
            "Extraction returned unrecognised field names that were discarded: "
            + ", ".join(sorted(set(unknown_fields))[:6])
        )
    return profile


# --------------------------------------------------------------------------
# Deterministic fallback
# --------------------------------------------------------------------------

#: Keyword cues -> profile field. Order matters within a field: the first block
#: matching any cue wins. Kept blunt on purpose; this path claims little.
_CUES: dict[str, tuple[str, ...]] = {
    "problem_addressed": ("the problem", "problem statement", "pain point", "why this matters"),
    "core_product": ("our solution", "the solution", "product overview", "what we do", "how it works"),
    "business_model": ("business model", "revenue model", "how we make money", "unit economics"),
    "pricing_model": ("pricing", "price point", "subscription", "per seat", "per provider"),
    "traction": ("traction", "milestones", "progress to date", "results"),
    "target_customer": ("customer", "who we serve", "target market", "our users"),
    "distribution_model": ("go to market", "go-to-market", "gtm", "sales strategy", "channel"),
    "key_ip": ("intellectual property", "patent", "ip strategy", "defensibility", "moat"),
    "known_competitors": ("competit", "landscape", "alternatives", "vs."),
    "geographic_markets": ("geograph", "markets we serve", "expansion"),
    "partnerships": ("partner", "alliances"),
    "raise_amount": ("the ask", "we are raising", "raising", "use of proceeds"),
    "claimed_differentiation": ("why we win", "differentiat", "our advantage", "unique"),
}

_MONEY_RE = re.compile(r"\$\s?\d[\d,.]*\s?(?:[KMB]|million|billion|thousand)?", re.IGNORECASE)
_URL_RE = re.compile(r"\b((?:https?://)?[a-z0-9][a-z0-9\-]*\.(?:com|io|ai|co|health|net|org|us|example)(?:/\S*)?)\b", re.IGNORECASE)
_STAGE_RE = re.compile(
    r"\b(pre[- ]?seed|seed|series\s?[a-e]|bridge|convertible note|safe)\b", re.IGNORECASE
)
_ARR_RE = re.compile(r"\b(arr|mrr|annual recurring revenue|revenue)\b[^.\n]{0,40}", re.IGNORECASE)


def extract_heuristic(ingestion: IngestionResult, diagnostics: RunDiagnostics) -> CompanyProfile:
    """Deterministic extraction. Conservative by design."""
    profile = CompanyProfile()
    doc = ingestion.primary
    if doc is None:
        return profile

    def add(field: str, value: str, locator: str, confidence: float) -> None:
        if is_nullish(value):
            return
        value = re.sub(r"\s+", " ", value).strip()[:280]
        fact = make_fact(
            value=value,
            locator=locator,
            source_type="pitch_deck",
            evidence_type=grade_claim(value, SourceType.PITCH_DECK).value,
            confidence=confidence,
        )
        setattr(profile, field, merge_fact(getattr(profile, field), fact))

    # Company name: title of the first slide, or PDF metadata.
    first_titles = [b for b in doc.blocks if b.kind == "title" and b.index <= 1]
    if first_titles:
        add("company_name", first_titles[0].text.split("\n")[0], first_titles[0].locator, 0.6)
    elif doc.metadata.get("Title"):
        add("company_name", doc.metadata["Title"], "File metadata", 0.4)

    for block in doc.blocks:
        text = block.text
        low = text.lower()

        # Title-cued sections.
        if block.kind in {"title", "heading"}:
            for field, cues in _CUES.items():
                if any(cue in low for cue in cues) and not getattr(profile, field).known:
                    # The value is the *body* of the cued unit, not its heading.
                    body = " ".join(
                        b.text
                        for b in doc.blocks
                        if b.index == block.index and b.kind in {"body", "table"}
                    )
                    if body:
                        add(field, body, block.locator, 0.45)

        if not profile.website.known:
            m = _URL_RE.search(text)
            if m and "@" not in m.group(1):
                add("website", m.group(1), block.locator, 0.5)

        if not profile.funding_stage.known:
            m = _STAGE_RE.search(text)
            if m:
                add("funding_stage", m.group(1).title(), block.locator, 0.55)

        if not profile.raise_amount.known and re.search(r"\brais\w+\b", low):
            m = _MONEY_RE.search(text)
            if m:
                add("raise_amount", m.group(0), block.locator, 0.5)

        if not profile.revenue.known:
            m = _ARR_RE.search(text)
            if m:
                window = text[m.start(): m.start() + 90]
                if _MONEY_RE.search(window):
                    add("revenue", window, block.locator, 0.45)

    diagnostics.note(
        f"Heuristic extraction recovered {len(profile.known_fields())} of "
        f"{len(PROFILE_FIELDS)} profile fields."
    )
    return profile


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def merge_profiles(base: CompanyProfile, overlay: CompanyProfile) -> CompanyProfile:
    """Field-by-field merge under `merge_fact` precedence rules."""
    merged = base.model_copy(deep=True)
    for name in PROFILE_FIELDS:
        setattr(merged, name, merge_fact(getattr(merged, name), getattr(overlay, name)))
    seen = set(merged.extraction_notes)
    merged.extraction_notes += [n for n in overlay.extraction_notes if n not in seen]
    return merged


def build_profile(
    ingestion: IngestionResult,
    transcript: str,
    user: UserInputs,
    client: LLMClient,
    diagnostics: RunDiagnostics,
    settings: Optional[Settings] = None,
) -> CompanyProfile:
    """Produce the best profile available given credentials and source quality."""
    settings = settings or default_settings

    # Heuristic first: it is free, deterministic, and gives the LLM path
    # something to be merged against rather than trusted blindly.
    profile = extract_heuristic(ingestion, diagnostics)

    if client.available and transcript.strip():
        llm_profile = extract_with_llm(transcript, client, diagnostics, user)
        if llm_profile is not None:
            profile = merge_profiles(profile, llm_profile)
            diagnostics.llm_calls += 1
        else:
            diagnostics.degraded = True
    elif not client.available:
        diagnostics.degraded = True
        diagnostics.warn(
            "Running without a language model: profile extraction used keyword heuristics only, "
            "and confidence is correspondingly low."
        )

    # The operator has the last word.
    for name, fact in user.as_profile_overrides().items():
        setattr(profile, name, merge_fact(getattr(profile, name), fact))

    if not profile.company_name.known:
        diagnostics.warn(
            "Company name could not be determined from the deck. "
            "Enter it manually for a correctly titled report."
        )

    critical = ["target_customer", "core_product", "problem_addressed"]
    absent = [c for c in critical if not profile.get(c).known]
    if absent:
        diagnostics.warn(
            "Core positioning fields missing from the source material: "
            + ", ".join(a.replace("_", " ") for a in absent)
            + ". Competitor discovery will be less precise."
        )

    return profile
