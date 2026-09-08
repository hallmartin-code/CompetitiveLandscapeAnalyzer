"""
Whitespace analysis (spec section 10).

The discipline here is that a whitespace opportunity must be *derived from the
competitive set*, not brainstormed. If none of the identified competitors serves
a segment, that is whitespace; if the company simply has not built a feature
yet, that is a roadmap item and does not belong on an investor one-pager.

Each opportunity therefore has to name the evidence -- typically the gap in the
competitor data that implies it.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel, Field

from config import Settings, settings as default_settings
from llm.client import LLMClient
from models.schemas import (
    CompanyProfile,
    Competitor,
    EvidenceType,
    RunDiagnostics,
    WhitespaceOpportunity,
)

logger = logging.getLogger(__name__)


class OpportunityInput(BaseModel):
    opportunity: str = Field(description="The opening, stated in <=14 words.")
    evidence: str = Field(
        description="The specific gap in the competitive set or source material that implies it."
    )
    rationale: str = Field(description="Why this company in particular could take it. <=25 words.")
    difficulty: str = Field(description="low, medium or high.")
    investor_relevance: str = Field(
        description="Why an investor should care: what it changes about the return case. <=20 words."
    )


class WhitespaceResult(BaseModel):
    opportunities: List[OpportunityInput]
    rejected: List[str] = Field(
        default_factory=list,
        description="Apparent openings rejected as roadmap items or already served, with reasons.",
    )


_WHITESPACE_RULES = """\
WHAT COUNTS AS WHITESPACE
An opening in the market that the identified competitors do not currently serve,
and that this company is credibly positioned to take. Categories to consider:
underserved customer segments; unserved geographies; workflow gaps; pricing
gaps; distribution gaps; product integration gaps; regulatory openings; data
advantages; platform opportunities.

WHAT DOES NOT COUNT
- A feature the company has not built yet. That is a roadmap item.
- A segment already served by one of the listed competitors.
- A generic "expand to adjacent verticals" statement with no specific gap.
- An opportunity the company has no credible path to take.

RULES
- Ground each opportunity in the competitive set. The evidence field must point
  at what is missing from the competitors, or at a specific fact in the material.
- Prefer openings that are defensible, not merely unoccupied. An empty segment
  that anyone could enter tomorrow is weak whitespace; say so in difficulty.
- Be specific. "SMB market" is not an opportunity; "practices under 5 providers,
  which every listed competitor prices out" is.
"""


def analyse(
    profile: CompanyProfile,
    competitors: List[Competitor],
    client: LLMClient,
    diagnostics: RunDiagnostics,
    settings: Optional[Settings] = None,
) -> List[WhitespaceOpportunity]:
    """Identify 3-5 grounded whitespace opportunities."""
    settings = settings or default_settings

    if not client.available:
        diagnostics.note(
            "Whitespace analysis skipped: it requires reasoning over the competitive set, "
            "which is unavailable without a language model."
        )
        return []
    if not competitors:
        diagnostics.note("Whitespace analysis skipped: no competitive set to find gaps in.")
        return []

    listing = "\n".join(
        f"- {c.name} [{c.category.value}]: serves {c.target_customer[:50]}; "
        f"product {c.core_product[:60]}; pricing {c.pricing[:35]}; "
        f"reach {c.geographic_reach[:35]}; weaknesses: {'; '.join(c.weaknesses[:2])[:90]}"
        for c in competitors
    )
    subject_lines = []
    for name in [
        "core_product", "target_customer", "buyer", "business_model", "pricing_model",
        "distribution_model", "geographic_markets", "proprietary_data", "key_ip",
        "regulatory_advantages", "customers",
    ]:
        fact = profile.get(name)
        if fact.known:
            subject_lines.append(f"- {name.replace('_',' ')}: {fact.value[:130]}")

    prompt = (
        f"Identify {settings.whitespace_count} market whitespace opportunities for the subject "
        "company, derived from gaps in the competitive set below.\n\n"
        f"{_WHITESPACE_RULES}\n"
        f"===== SUBJECT COMPANY: {profile.display_name()} =====\n"
        + "\n".join(subject_lines)
        + f"\n\n===== COMPETITIVE SET =====\n{listing}\n===== END ====="
    )

    result, error = client.structured(
        prompt, WhitespaceResult, label="whitespace", max_tokens=12000
    )
    if error or result is None:
        diagnostics.warn(f"Whitespace analysis failed: {error}")
        return []

    diagnostics.llm_calls += 1

    opportunities: List[WhitespaceOpportunity] = []
    for item in result.opportunities[: settings.whitespace_count + 1]:
        difficulty = item.difficulty.strip().lower()
        if difficulty not in {"low", "medium", "high"}:
            difficulty = "medium"
        opportunities.append(
            WhitespaceOpportunity(
                opportunity=item.opportunity.strip(),
                evidence=item.evidence.strip(),
                rationale=item.rationale.strip(),
                difficulty=difficulty,
                investor_relevance=item.investor_relevance.strip(),
                # Whitespace is by construction a derived conclusion.
                evidence_type=EvidenceType.ANALYST_INFERENCE,
            )
        )

    if result.rejected:
        diagnostics.note("Rejected as non-whitespace: " + "; ".join(result.rejected[:3]))
    if len(opportunities) < 3:
        diagnostics.note(
            f"Only {len(opportunities)} defensible whitespace opportunit(ies) identified — "
            "a crowded market with few open positions is itself a finding."
        )

    logger.info("Identified %d whitespace opportunities", len(opportunities))
    return opportunities
