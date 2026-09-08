"""
Positioning recommendation (spec section 12).

Produces the investor-facing positioning statement in the canonical form:

    For [target customer], [Company] is the [category] that [primary benefit],
    unlike [alternative], because [defensible differentiation].

Two constraints are enforced in code rather than trusted to the prompt:

  * **Length.** The statement must land in 20-35 words. Longer and it stops
    working as a positioning line; the word count is checked and a violation is
    reported rather than shipped silently.
  * **Evidence.** The "because" clause must rest on something the analysis
    actually established. `claims_to_avoid` exists to name the company's own
    unevidenced superlatives explicitly, so the founder is told which lines to
    stop using -- the most directly actionable output in the report.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from pydantic import BaseModel, Field

from extraction.evidence import is_unevidenced_superlative
from llm.client import LLMClient
from models.schemas import (
    UNKNOWN,
    CompanyProfile,
    Competitor,
    DifferentiationScore,
    DimensionAssessment,
    Positioning,
    RunDiagnostics,
)

logger = logging.getLogger(__name__)

MIN_WORDS = 20
MAX_WORDS = 35

#: Words too common to indicate that two claims are about the same thing.
_STOPWORDS = frozenset(
    "the a an and or of to for is are was were be been it its this that with "
    "than as at by on in from only solution company platform product".split()
)


def _covers(snippet: str, existing: str) -> bool:
    """True when `existing` already makes the point `snippet` would make."""

    def keys(text: str) -> set:
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        return {t for t in tokens if t not in _STOPWORDS and len(t) > 2}

    a, b = keys(snippet), keys(existing)
    if not a:
        return False
    return len(a & b) >= max(2, int(len(a) * 0.5))


class PositioningResult(BaseModel):
    current_implied: str = Field(
        description="How the material currently positions the company, in <=20 words. "
        "Describe what it actually says, including if it is vague."
    )
    recommended: str = Field(
        description="The recommended positioning statement, 20-35 words, in the canonical form."
    )
    proof_points_required: List[str] = Field(
        description="2-4 specific proof points needed to make the statement credible."
    )
    claims_to_avoid: List[str] = Field(
        description="2-4 claims the company should stop making, each with the reason."
    )
    competitive_narrative: str = Field(
        description="The one-sentence competitive story an investor will grasp immediately."
    )


_POSITIONING_RULES = """\
Produce an investor-facing positioning recommendation.

CANONICAL FORM (use it unless the market makes it genuinely unnatural):
"For [target customer], [Company] is the [category] that [primary benefit],
unlike [alternative], because [defensible differentiation]."

HARD CONSTRAINTS
- The recommended statement must be between 20 and 35 words. Count them.
- The "unlike" clause must name a real alternative from the competitive set --
  including the status quo where that is the true alternative.
- The "because" clause must rest on something the analysis established. If the
  only durable asset is an accumulating dataset, say that. If nothing durable is
  established, position on the strongest evidenced advantage and list the proof
  points needed rather than inventing a moat.

PROOF POINTS
Specific and checkable: a metric with a denominator, a signed agreement, an
issued patent, a third-party benchmark. Not "customer testimonials".

CLAIMS TO AVOID
Name the company's own unevidenced or unsupportable claims and say why each
fails: uncited superlatives, "only solution" claims that are false given the
competitive set, and any moat language the evidence does not support.

TONE
Institutional investment research. No marketing language, no adjectives that
carry no information.
"""


def recommend(
    profile: CompanyProfile,
    competitors: List[Competitor],
    dimensions: List[DimensionAssessment],
    score: DifferentiationScore,
    client: LLMClient,
    diagnostics: RunDiagnostics,
) -> Positioning:
    """Generate the positioning recommendation."""
    if not client.available:
        return _fallback_positioning(profile, competitors, diagnostics)

    from models.schemas import Advantage

    strengths = [
        f"- {d.dimension}: {d.company_position[:80]} "
        f"(evidence: {d.evidence_type.value}, durability: {d.sustainability})"
        for d in dimensions
        if d.advantage is Advantage.COMPANY
    ] or ["- No dimension shows a clear, evidenced company advantage."]

    alternatives = "\n".join(
        f"- {c.name} [{c.category.value}]: {c.positioning[:70]}" for c in competitors[:7]
    )

    claim = profile.claimed_differentiation
    claim_line = (
        f"\nThe company's own differentiation claim: \"{claim.value}\" "
        f"[{claim.evidence_type.value}]"
        if claim.known
        else "\nThe material makes no explicit differentiation claim."
    )

    prompt = (
        f"{_POSITIONING_RULES}\n"
        f"===== COMPANY =====\n"
        f"Name: {profile.display_name()}\n"
        f"Target customer: {profile.target_customer.display('not established')}\n"
        f"Buyer: {profile.buyer.display('not established')}\n"
        f"Product: {profile.core_product.display('not established')}\n"
        f"Category: {profile.product_category.display('not established')}\n"
        f"Problem: {profile.problem_addressed.display('not established')}\n"
        f"Business model: {profile.business_model.display('not established')}"
        f"{claim_line}\n\n"
        f"===== EVIDENCED ADVANTAGES =====\n" + "\n".join(strengths[:8]) + "\n\n"
        f"===== ALTERNATIVES =====\n{alternatives}\n\n"
        f"Differentiation score: {score.total}/100 ({score.band}).\n"
        "===== END ====="
    )

    result, error = client.structured(
        prompt, PositioningResult, label="positioning", max_tokens=8000
    )
    if error or result is None:
        diagnostics.warn(f"Positioning recommendation failed: {error}")
        return _fallback_positioning(profile, competitors, diagnostics)

    diagnostics.llm_calls += 1

    statement = re.sub(r"\s+", " ", result.recommended).strip()
    words = len(statement.split())
    if not (MIN_WORDS <= words <= MAX_WORDS):
        diagnostics.note(
            f"Recommended positioning statement is {words} words "
            f"(target {MIN_WORDS}-{MAX_WORDS}); it is reported as generated."
        )

    claims = list(result.claims_to_avoid)
    # Always surface the company's own uncited superlative, even if the model
    # did not flag it -- this is the check the founder most needs. Substring
    # matching is too brittle to detect that the model already said it in its
    # own words, so compare on distinctive word overlap instead.
    if claim.known and is_unevidenced_superlative(claim.value):
        snippet = claim.value[:70]
        if not any(_covers(snippet, c) for c in claims):
            claims.insert(0, f'"{snippet}" — uncited superlative; investors will discount it.')

    return Positioning(
        current_implied=result.current_implied.strip() or UNKNOWN,
        recommended=statement,
        proof_points_required=[p.strip() for p in result.proof_points_required if p.strip()][:4],
        claims_to_avoid=[c.strip() for c in claims if c.strip()][:4],
        competitive_narrative=result.competitive_narrative.strip(),
        word_count=words,
    )


def _fallback_positioning(
    profile: CompanyProfile, competitors: List[Competitor], diagnostics: RunDiagnostics
) -> Positioning:
    """Template positioning assembled from established facts only.

    Where a slot is not established, it says so rather than filling it in --
    a positioning statement with visible holes is more useful than a fluent
    invention.
    """
    diagnostics.note("Positioning assembled from a template (no model available).")

    customer = _slot(profile.target_customer.value, "[target customer not established]", 8)
    category = _slot(
        profile.product_category.value or profile.sector.value,
        "[category not established]",
        6,
    )
    benefit = _slot(profile.core_product.value, "[primary benefit not established]", 12)
    alternative = _slot(
        competitors[0].name if competitors else None, "the current manual process", 5
    )
    because = _slot(
        profile.key_ip.value or profile.proprietary_data.value,
        "[defensible differentiation not established]",
        12,
    )

    statement = (
        f"For {customer}, {profile.display_name()} is the {category} that {benefit}, "
        f"unlike {alternative}, because {because}."
    )
    statement = re.sub(r"\s+", " ", statement).strip()
    words = len(statement.split())
    if words > MAX_WORDS:
        diagnostics.note(
            f"Template positioning statement is {words} words (target "
            f"{MIN_WORDS}-{MAX_WORDS}); the source fields were too verbose to compress "
            "without a model. Treat it as a structural skeleton, not a finished line."
        )

    claims: List[str] = []
    claim = profile.claimed_differentiation
    if claim.known and is_unevidenced_superlative(claim.value):
        claims.append(f'"{claim.value[:70]}" — uncited superlative; investors will discount it.')

    return Positioning(
        current_implied=claim.display("Not established in the material."),
        recommended=statement,
        proof_points_required=[
            f"Evidence for each bracketed gap above ({len(profile.missing_fields())} profile fields unestablished).",
            "Third-party validation of the primary performance claim.",
        ],
        claims_to_avoid=claims,
        competitive_narrative="Generated without model analysis; treat as a structural template only.",
        word_count=words,
    )


def _slot(value: Optional[str], fallback: str, max_words: int) -> str:
    """Compress a profile value into a positioning slot.

    The heuristic extractor yields whole slide bodies, which would otherwise
    produce a 100-word "positioning statement". Take the first clause and cap
    the word count so the template stays recognisably a positioning line.
    """
    from models.schemas import is_nullish

    if is_nullish(value):
        return fallback
    text = re.sub(r"\s+", " ", str(value)).strip()
    # First clause only: these fields often hold several stacked bullet points.
    text = re.split(r"(?<=[a-z0-9])[.;·]\s+|\s+[—–]\s+", text)[0].strip()
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words])
    return text.rstrip(" ,;:.")
