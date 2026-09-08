"""
Investor concerns and diligence questions (spec section 11).

The spec is emphatic that three things stay separate, and the schema enforces it:

    current_risk       -- what is true today, given the evidence
    diligence_question -- what to ask the founder
    evidence_needed    -- what would actually settle it

They collapse easily in practice ("Risk: unclear defensibility. Question: is it
defensible?") and the result is useless in a partner meeting. Keeping them as
three required fields, with a rule that the question must be answerable and the
evidence must be a specific artefact, is what makes the output usable.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel, Field

from config import Settings, settings as default_settings
from llm.client import LLMClient
from models.schemas import (
    Advantage,
    CompanyProfile,
    Competitor,
    DifferentiationScore,
    DimensionAssessment,
    EvidenceType,
    InvestorConcern,
    RunDiagnostics,
)

logger = logging.getLogger(__name__)


class ConcernInput(BaseModel):
    concern: str = Field(description="The competitive question, as an investor would phrase it. <=12 words.")
    current_risk: str = Field(
        description="What the evidence establishes today. State the position, not the question. <=28 words."
    )
    diligence_question: str = Field(
        description="One specific question to put to the founder. Must be answerable in a meeting."
    )
    evidence_needed: str = Field(
        description="The specific artefact or data that would settle it — a document, a metric, "
        "a reference call, a contract. Not 'more information'."
    )
    severity: str = Field(description="high, medium or low.")


class ConcernsResult(BaseModel):
    concerns: List[ConcernInput]


_CONCERN_RULES = """\
Produce the competitive questions this deal will actually face in an investment
committee. Draw them from the assessed weaknesses below, not from a generic list.

Typical shapes (use only where the evidence supports them):
- Why will an incumbent not simply replicate this?
- Why will customers switch, and what does switching actually cost them?
- Is the claimed differentiation material, or a feature difference?
- Can a competitor undercut on price without structural disadvantage?
- Is there a durable distribution advantage, or is distribution the weak point?
- Is the proprietary technology or data genuinely defensible?
- Is there platform dependency on a party that could become a competitor?
- Is customer acquisition structurally harder than management assumes?

RULES
1. current_risk states a position, not a question. It must reflect what the
   evidence shows -- including when the evidence is simply absent.
2. diligence_question must be answerable by a founder in a meeting. Not
   "is this defensible?" but "which three payers have you integrated via FHIR,
   and what did the fourth cost you in engineering weeks?"
3. evidence_needed must name a specific artefact: a signed contract, a cohort
   retention curve, a patent examiner's first office action, three reference
   calls with named customers. Never "further diligence" or "more data".
4. Rank by what would most change an investment decision. Severity 'high' means
   an unsatisfactory answer could justify passing.
5. Do not invent risks the evidence does not support, and do not soften ones it does.
"""


def identify(
    profile: CompanyProfile,
    competitors: List[Competitor],
    dimensions: List[DimensionAssessment],
    score: DifferentiationScore,
    client: LLMClient,
    diagnostics: RunDiagnostics,
    settings: Optional[Settings] = None,
) -> List[InvestorConcern]:
    """Produce the prioritised investor watchlist."""
    settings = settings or default_settings

    if not client.available:
        return _fallback_concerns(profile, dimensions, diagnostics)

    weak = [
        d for d in dimensions
        if d.advantage in {Advantage.COMPETITOR, Advantage.UNKNOWN}
        or d.evidence_type in {EvidenceType.COMPANY_CLAIM, EvidenceType.MISSING}
    ]
    weak_lines = "\n".join(
        f"- {d.dimension}: advantage={d.advantage.value}, evidence={d.evidence_type.value}, "
        f"sustainability={d.sustainability} | {d.evidence[:110]}"
        for d in weak[:14]
    )
    threat_lines = "\n".join(
        f"- {c.name} [{c.category.value}, threat {c.threat_level.value}]: {c.threat_rationale[:110]}"
        for c in competitors[:8]
    )
    discounted = "\n".join(f"- {x}" for x in score.unevidenced_claims_discounted[:6])

    prompt = (
        f"Produce the {settings.concern_count} competitive concerns an investment committee "
        f"would raise about {profile.display_name()}.\n\n"
        f"{_CONCERN_RULES}\n"
        f"Differentiation score: {score.total}/100 ({score.band}), "
        f"confidence {score.confidence:.2f}.\n\n"
        f"===== ASSESSED WEAKNESSES =====\n{weak_lines}\n\n"
        f"===== COMPETITIVE THREATS =====\n{threat_lines}\n"
        + (f"\n===== CLAIMS EXCLUDED FOR LACK OF EVIDENCE =====\n{discounted}\n" if discounted else "")
        + "===== END ====="
    )

    result, error = client.structured(
        prompt, ConcernsResult, label="investor-concerns", max_tokens=12000
    )
    if error or result is None:
        diagnostics.warn(f"Investor concern analysis failed: {error}")
        return _fallback_concerns(profile, dimensions, diagnostics)

    diagnostics.llm_calls += 1

    concerns: List[InvestorConcern] = []
    for item in result.concerns[: settings.concern_count + 1]:
        severity = item.severity.strip().lower()
        if severity not in {"high", "medium", "low"}:
            severity = "medium"
        concerns.append(
            InvestorConcern(
                concern=item.concern.strip(),
                current_risk=item.current_risk.strip(),
                diligence_question=item.diligence_question.strip(),
                evidence_needed=item.evidence_needed.strip(),
                severity=severity,
            )
        )

    order = {"high": 0, "medium": 1, "low": 2}
    concerns.sort(key=lambda c: order[c.severity])
    logger.info("Identified %d investor concerns", len(concerns))
    return concerns


def _fallback_concerns(
    profile: CompanyProfile,
    dimensions: List[DimensionAssessment],
    diagnostics: RunDiagnostics,
) -> List[InvestorConcern]:
    """Deterministic concerns derived from what the material failed to establish.

    Not a substitute for analysis, but an honest one: every item here is
    generated because a specific field is absent, and says so.
    """
    diagnostics.note("Investor concerns derived from missing-data rules (no model available).")

    templates = [
        (
            "key_ip",
            "Is the technology defensible?",
            "No intellectual property position is established in the material.",
            "What protects this from a competitor rebuilding it in two quarters?",
            "Patent filings with claim scope and status, or a written trade-secret position.",
            "high",
        ),
        (
            "distribution_model",
            "Is there a distribution advantage?",
            "No distribution strategy is established in the material.",
            "How do you reach buyers today, and what does each new customer cost to acquire?",
            "CAC by channel with payback period, and any signed channel agreements.",
            "high",
        ),
        (
            "switching_costs",
            "Why will customers switch and stay?",
            "Switching costs are not established in the material.",
            "What does a customer have to rip out to leave you?",
            "Cohort retention by month and a churned-customer post-mortem.",
            "high",
        ),
        (
            "pricing_model",
            "Can competitors undercut on price?",
            "Pricing is not established in the material.",
            "What is your list price, and what happens to the deal if a rival halves it?",
            "Price book, discounting history, and gross margin by contract.",
            "medium",
        ),
        (
            "customers",
            "Is there real customer validation?",
            "Customer traction is not established in the material.",
            "Which customers are in production and paying, and who would take a reference call?",
            "Named customer list with contract values and three reference calls.",
            "high",
        ),
    ]

    concerns: List[InvestorConcern] = []
    for field, concern, risk, question, evidence, severity in templates:
        if not profile.get(field).known:
            concerns.append(
                InvestorConcern(
                    concern=concern,
                    current_risk=risk,
                    diligence_question=question,
                    evidence_needed=evidence,
                    severity=severity,
                )
            )

    if not concerns:
        concerns.append(
            InvestorConcern(
                concern="Is the claimed differentiation material?",
                current_risk="Differentiation rests on company claims that were not independently assessed.",
                diligence_question="Which of your advantages would survive a well-funded competitor copying it?",
                evidence_needed="Third-party benchmark, or customer references stating why they chose you.",
                severity="high",
            )
        )
    return concerns[:5]
