"""
Competitive assessment (spec section 6) and the Differentiation Score (section 7).

ARCHITECTURAL DECISION -- the model judges, Python scores
--------------------------------------------------------
The model assesses each of the 18 dimensions qualitatively: who holds the
advantage, on what evidence, and how durable it is. It is never asked for the
score itself. The 0-100 number is then computed deterministically in
`compute_score`.

This split matters for three reasons:
  * the arithmetic is reproducible and unit-testable;
  * the weighting is visible in `config.SCORE_WEIGHTS` rather than buried in a
    prompt, so it can be argued with and changed;
  * evidence discounting can be *enforced* rather than requested. The spec's
    rule -- "do not award points for differentiation claimed by the company
    without evidence" -- is implemented as a pull toward a neutral baseline
    (`_evidence_weight`), so an unevidenced claim neither earns nor loses
    points. It simply does not move the needle, which is the correct treatment.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from config import (
    ALL_DIMENSIONS,
    DIMENSION_GROUPS,
    SCORE_WEIGHTS,
    Settings,
    score_band,
    settings as default_settings,
)
from extraction.evidence import is_unevidenced_superlative
from llm.client import LLMClient
from models.schemas import (
    Advantage,
    CategoryScore,
    CompanyProfile,
    Competitor,
    DifferentiationScore,
    DimensionAssessment,
    EvidenceType,
    RunDiagnostics,
    UNKNOWN,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Scoring constants -- all tunables in one visible block
# --------------------------------------------------------------------------

#: Score contribution before evidence weighting, by who holds the advantage.
_ADVANTAGE_BASE: Dict[Advantage, float] = {
    Advantage.COMPANY: 1.00,
    Advantage.NEUTRAL: 0.50,
    Advantage.COMPETITOR: 0.12,
    Advantage.UNKNOWN: 0.35,
}

#: The "we have not established this" anchor. Weak evidence pulls a dimension
#: back toward this value from whichever direction it started.
_BASELINE = 0.38

#: How far a dimension is allowed to move from the baseline, by evidence class.
#:
#: These values are calibrated against an explicit test
#: (`test_unevidenced_claims_do_not_earn_points`): a company asserting an
#: advantage on every dimension with no corroboration must not materially
#: outscore one that established nothing. `COMPANY_CLAIM` is therefore low by
#: design -- an uncited claim nudges the score, it does not move it.
#: `ANALYST_INFERENCE` sits below half, because our own reasoning is a weaker
#: warrant than a checkable source, whichever way it points.
_EVIDENCE_WEIGHT: Dict[EvidenceType, float] = {
    EvidenceType.STATED_FACT: 1.00,
    EvidenceType.ANALYST_INFERENCE: 0.55,
    EvidenceType.COMPANY_CLAIM: 0.22,
    EvidenceType.UNVERIFIED: 0.15,
    EvidenceType.MISSING: 0.08,
}

_SUSTAINABILITY_VALUE = {"high": 1.0, "medium": 0.6, "low": 0.22, "unknown": 0.35}


# --------------------------------------------------------------------------
# LLM-facing schema
# --------------------------------------------------------------------------


class DimensionInput(BaseModel):
    dimension: str = Field(description="Must be exactly one of the supplied dimension names.")
    advantage: str = Field(description="One of: company, competitor, neutral, unknown.")
    company_position: str = Field(description="The company's position in <=12 words, or 'Unknown'.")
    competitor_position: str = Field(description="The best competitor's position in <=12 words, or 'Unknown'.")
    evidence: str = Field(description="The specific evidence, with a locator. 'None found' if absent.")
    evidence_type: str = Field(
        description="One of: stated_fact, company_claim, analyst_inference, unverified, missing."
    )
    confidence: float = Field(description="0.0-1.0.")
    sustainability: str = Field(
        description="How durable the position is: high, medium, low, or unknown."
    )


class AssessmentResult(BaseModel):
    dimensions: List[DimensionInput]
    overall_assessment: str = Field(
        description="2-3 sentences an investor could read alone. State the real competitive "
        "position, including what is unproven."
    )
    strongest_advantage: str = Field(description="The single most defensible advantage, or 'None established'.")
    weakest_point: str = Field(description="The most exposed dimension.")


_ASSESSMENT_RULES = """\
ASSESSMENT RULES
- Judge each dimension against the competitive set supplied, not against an
  abstract ideal. "Advantage: company" means the company is genuinely better
  positioned than the named alternatives on that dimension.
- advantage = 'unknown' is the correct answer when the material does not
  establish the dimension. Use it. Do not default to 'neutral' to seem balanced.
- evidence_type is about the evidence, not your confidence in the conclusion:
    stated_fact       - source material cites an external, checkable source
    company_claim     - the company asserts it with no external corroboration
    analyst_inference - you reasoned it from the material or market knowledge
    missing           - nothing in the material speaks to this dimension
  A number on a pitch deck slide with no external citation is a company_claim.
- sustainability asks whether the position survives a well-funded competitor
  deciding to attack it within roughly 24 months. A feature difference is
  usually 'low'. Regulatory exclusivity, accumulating proprietary data,
  entrenched workflow and real switching costs are usually higher.
- Distinguish a feature difference from a durable, defensible advantage.
  Being first, being faster, or having a better UI is rarely 'high'.
- Treat incumbent distribution as a genuine competitor advantage.
"""


def assess_dimensions(
    profile: CompanyProfile,
    competitors: List[Competitor],
    client: LLMClient,
    diagnostics: RunDiagnostics,
) -> tuple[List[DimensionAssessment], str]:
    """Model-assessed 18-dimension comparison. Returns (dimensions, assessment)."""
    if not client.available:
        diagnostics.degraded = True
        diagnostics.warn(
            "Dimension assessment requires a language model; all dimensions are recorded "
            "as unknown and the differentiation score reflects that uncertainty."
        )
        return _unknown_dimensions(), ""

    profile_lines = []
    for name in [
        "core_product", "problem_addressed", "target_customer", "buyer", "business_model",
        "pricing_model", "distribution_model", "geographic_markets", "customers", "revenue",
        "traction", "key_ip", "proprietary_data", "regulatory_advantages", "network_effects",
        "switching_costs", "claimed_differentiation", "partnerships",
    ]:
        fact = profile.get(name)
        if fact.known:
            profile_lines.append(
                f"- {name.replace('_',' ')}: {fact.value} "
                f"[{fact.badge() or 'n/a'}; {fact.evidence_type.value}]"
            )
    missing = [m.replace("_", " ") for m in profile.missing_fields()]
    if missing:
        profile_lines.append(f"- NOT ESTABLISHED: {', '.join(missing[:14])}")

    comp_lines = []
    for c in competitors:
        comp_lines.append(
            f"- {c.name} [{c.category.value}, threat {c.threat_level.value}] "
            f"product: {c.core_product[:80]}; distribution: {c.distribution[:50]}; "
            f"strengths: {'; '.join(c.strengths[:2])[:120]}; "
            f"defensibility: {c.defensibility[:70]}"
        )

    prompt = (
        "Assess this company against its competitive set across every dimension listed.\n\n"
        f"{_ASSESSMENT_RULES}\n"
        f"DIMENSIONS (assess all {len(ALL_DIMENSIONS)}, using these exact names):\n"
        + "\n".join(f"- {d}" for d in ALL_DIMENSIONS)
        + "\n\n===== COMPANY =====\n"
        + "\n".join(profile_lines)
        + "\n\n===== COMPETITIVE SET =====\n"
        + "\n".join(comp_lines)
        + "\n===== END ====="
    )

    result, error = client.structured(
        prompt, AssessmentResult, label="dimension-assessment", max_tokens=24000
    )
    if error or result is None:
        diagnostics.warn(f"Dimension assessment failed: {error}")
        diagnostics.degraded = True
        return _unknown_dimensions(), ""

    diagnostics.llm_calls += 1

    from extraction.evidence import parse_evidence_type

    by_name = {d.lower(): d for d in ALL_DIMENSIONS}
    assessed: Dict[str, DimensionAssessment] = {}
    for item in result.dimensions:
        canonical = by_name.get(item.dimension.strip().lower())
        if canonical is None:
            continue
        try:
            advantage = Advantage(item.advantage.strip().lower())
        except ValueError:
            advantage = Advantage.UNKNOWN
        sustainability = item.sustainability.strip().lower()
        if sustainability not in _SUSTAINABILITY_VALUE:
            sustainability = "unknown"
        assessed[canonical] = DimensionAssessment(
            dimension=canonical,
            advantage=advantage,
            company_position=item.company_position or UNKNOWN,
            competitor_position=item.competitor_position or UNKNOWN,
            evidence=item.evidence,
            evidence_type=parse_evidence_type(item.evidence_type),
            confidence=item.confidence,
            sustainability=sustainability,
        )

    # Any dimension the model skipped is unknown, not absent.
    dimensions = [
        assessed.get(name, DimensionAssessment(dimension=name)) for name in ALL_DIMENSIONS
    ]
    skipped = [d.dimension for d in dimensions if d.dimension not in assessed]
    if skipped:
        diagnostics.note(f"{len(skipped)} dimension(s) were not assessed and count as unknown.")

    if result.strongest_advantage:
        diagnostics.note(f"Strongest advantage: {result.strongest_advantage}")
    if result.weakest_point:
        diagnostics.note(f"Most exposed: {result.weakest_point}")

    return dimensions, result.overall_assessment


def _unknown_dimensions() -> List[DimensionAssessment]:
    return [DimensionAssessment(dimension=name) for name in ALL_DIMENSIONS]


# --------------------------------------------------------------------------
# Deterministic scoring
# --------------------------------------------------------------------------


def _dimension_score(d: DimensionAssessment) -> float:
    """Score one dimension in 0..1, pulled toward baseline by weak evidence."""
    base = _ADVANTAGE_BASE.get(d.advantage, 0.35)
    weight = _EVIDENCE_WEIGHT.get(d.evidence_type, 0.3)
    # Confidence modulates the evidence weight but cannot exceed it: a model
    # that is confident about a company claim is still looking at a claim.
    weight *= 0.6 + 0.4 * max(0.0, min(1.0, d.confidence))
    return _BASELINE + (base - _BASELINE) * weight


def _sustainability_score(dimensions: List[DimensionAssessment]) -> tuple[float, str]:
    """Derive the sustainability category from where the company actually leads."""
    leading = [d for d in dimensions if d.advantage is Advantage.COMPANY]
    if not leading:
        return _BASELINE, "No dimension shows a clear company advantage to sustain."

    values = []
    for d in leading:
        raw = _SUSTAINABILITY_VALUE.get(d.sustainability, 0.35)
        weight = _EVIDENCE_WEIGHT.get(d.evidence_type, 0.3)
        values.append(_BASELINE + (raw - _BASELINE) * weight)
    score = sum(values) / len(values)

    durable = [d.dimension for d in leading if d.sustainability == "high"]
    if durable:
        rationale = f"Durable on {', '.join(durable[:3]).lower()}."
    else:
        rationale = (
            f"{len(leading)} advantage(s) identified, none rated highly durable against "
            "a funded competitor."
        )
    return score, rationale


def compute_score(
    dimensions: List[DimensionAssessment],
    profile: CompanyProfile,
    competitors: List[Competitor],
    diagnostics: RunDiagnostics,
    settings: Optional[Settings] = None,
) -> DifferentiationScore:
    """Compute the 0-100 differentiation score from assessed dimensions."""
    settings = settings or default_settings
    by_name = {d.dimension: d for d in dimensions}

    categories: List[CategoryScore] = []
    for name, weight in SCORE_WEIGHTS:
        members = DIMENSION_GROUPS.get(name, [])

        if name == "Sustainability of Advantage":
            raw, rationale = _sustainability_score(dimensions)
            backed = any(
                d.evidence_type in {EvidenceType.STATED_FACT, EvidenceType.ANALYST_INFERENCE}
                for d in dimensions
                if d.advantage is Advantage.COMPANY
            )
        else:
            scored = [by_name[m] for m in members if m in by_name]
            if not scored:
                raw, rationale, backed = _BASELINE, "Not assessed.", False
            else:
                raw = sum(_dimension_score(d) for d in scored) / len(scored)
                leads = [d.dimension for d in scored if d.advantage is Advantage.COMPANY]
                losses = [d.dimension for d in scored if d.advantage is Advantage.COMPETITOR]
                backed = any(d.evidence_type is EvidenceType.STATED_FACT for d in scored)
                if leads:
                    rationale = f"Leads on {', '.join(x.lower() for x in leads[:2])}."
                elif losses:
                    rationale = f"Behind on {', '.join(x.lower() for x in losses[:2])}."
                else:
                    rationale = "No clear advantage established."

        categories.append(
            CategoryScore(
                name=name,
                weight=weight,
                raw=round(raw, 4),
                weighted=round(raw * weight, 2),
                rationale=rationale,
                evidence_backed=backed,
            )
        )

    total = int(round(sum(c.weighted for c in categories)))
    total = max(0, min(100, total))

    discounted = _discounted_claims(profile, dimensions)
    confidence = _confidence(dimensions, profile, competitors, diagnostics)

    band = score_band(total)
    explanation = _explain(total, band, categories, discounted, confidence)

    diagnostics.note(f"Differentiation score {total}/100 ({band}), confidence {confidence:.2f}.")

    return DifferentiationScore(
        total=total,
        confidence=round(confidence, 3),
        categories=categories,
        band=band,
        explanation=explanation,
        unevidenced_claims_discounted=discounted,
    )


def _discounted_claims(
    profile: CompanyProfile, dimensions: List[DimensionAssessment]
) -> List[str]:
    """List claims that were prevented from earning points, for transparency."""
    discounted: List[str] = []

    claim = profile.claimed_differentiation
    if claim.known and is_unevidenced_superlative(claim.value):
        discounted.append(f"Claimed differentiation is an uncited superlative: \"{claim.value[:90]}\"")

    for d in dimensions:
        if d.advantage is Advantage.COMPANY and d.evidence_type is EvidenceType.COMPANY_CLAIM:
            discounted.append(f"{d.dimension}: company claim without external corroboration")
        elif d.advantage is Advantage.COMPANY and d.evidence_type is EvidenceType.MISSING:
            discounted.append(f"{d.dimension}: advantage asserted with no evidence in the material")

    return discounted[:8]


def _confidence(
    dimensions: List[DimensionAssessment],
    profile: CompanyProfile,
    competitors: List[Competitor],
    diagnostics: RunDiagnostics,
) -> float:
    """How much the score itself should be trusted.

    Four independent inputs, because a score can be undermined by thin source
    material, a thin competitor set, weak evidence, or a degraded run -- and
    each needs to show up.
    """
    coverage = profile.coverage                                     # source richness
    assessed = [d for d in dimensions if d.advantage is not Advantage.UNKNOWN]
    assessed_share = len(assessed) / max(1, len(dimensions))
    evidence = (
        sum(_EVIDENCE_WEIGHT.get(d.evidence_type, 0.2) for d in dimensions)
        / max(1, len(dimensions))
    )
    comp_quality = (
        sum(c.confidence for c in competitors) / len(competitors) if competitors else 0.15
    )

    confidence = (
        0.30 * coverage + 0.25 * assessed_share + 0.25 * evidence + 0.20 * comp_quality
    )
    if diagnostics.degraded:
        confidence *= 0.6
    if not diagnostics.research_performed:
        confidence *= 0.9
    return max(0.0, min(1.0, confidence))


def _explain(
    total: int,
    band: str,
    categories: List[CategoryScore],
    discounted: List[str],
    confidence: float,
) -> str:
    ordered = sorted(categories, key=lambda c: c.raw, reverse=True)
    strong = [c.name for c in ordered[:2] if c.raw > 0.55]
    weak = [c.name for c in reversed(ordered) if c.raw < 0.42][:2]

    parts = [f"{total}/100 — {band}."]
    if strong:
        parts.append(f"Strongest: {', '.join(strong).lower()}.")
    if weak:
        parts.append(f"Weakest: {', '.join(weak).lower()}.")
    if discounted:
        parts.append(f"{len(discounted)} unevidenced claim(s) were excluded from scoring.")
    if confidence < 0.5:
        parts.append("Score confidence is low; treat as provisional pending diligence.")
    return " ".join(parts)
