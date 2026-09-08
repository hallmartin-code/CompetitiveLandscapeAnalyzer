"""
Shared fixtures.

Every test in this suite runs offline. No fixture constructs a real `LLMClient`
with credentials, and `_offline` below strips model credentials from the
environment for the whole session, so a test can never silently start billing
the API or become dependent on network conditions.

The model-backed paths are covered by injecting a `FakeLLM` that returns
canned, schema-valid payloads -- that is what makes the mapping code between
model output and internal models testable at all.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, List, Optional, Tuple, Type

import pytest
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Settings  # noqa: E402
from models.schemas import (  # noqa: E402
    Advantage,
    CompanyProfile,
    CompetitiveAnalysis,
    Competitor,
    CompetitorCategory,
    Consent,
    DimensionAssessment,
    Evidence,
    EvidenceType,
    Fact,
    InvestorConcern,
    MarketAxis,
    MarketMap,
    MarketMapEntry,
    Positioning,
    RunDiagnostics,
    SourceType,
    ThreatLevel,
    WhitespaceOpportunity,
)

SAMPLE_DIR = ROOT / "samples"


@pytest.fixture(scope="session", autouse=True)
def _offline():
    """Guarantee the suite never reaches the Anthropic API."""
    saved = {
        k: os.environ.pop(k, None)
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")
    }
    yield
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v


@pytest.fixture(scope="session")
def sample_decks() -> dict:
    """Build the sample deck pair once per session if it is not already there."""
    pptx = SAMPLE_DIR / "sample_pitch_deck.pptx"
    pdf = SAMPLE_DIR / "sample_pitch_deck.pdf"
    if not pptx.exists() or not pdf.exists():
        sys.path.insert(0, str(SAMPLE_DIR))
        from make_sample_deck import build_pdf, build_pptx

        build_pptx(pptx)
        build_pdf(pdf)
    return {"pptx": pptx, "pdf": pdf}


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings()
    s.output_dir = tmp_path
    s.api_key = None
    return s


@pytest.fixture
def diagnostics() -> RunDiagnostics:
    return RunDiagnostics()


# --------------------------------------------------------------------------
# Fake model client
# --------------------------------------------------------------------------


class FakeLLM:
    """Stands in for `LLMClient`, returning canned schema-valid payloads.

    `responses` maps a call label to either a payload dict (validated against
    the caller's own output model, so a schema drift breaks the test) or an
    error string, which exercises the degradation path.
    """

    def __init__(self, responses: Optional[dict] = None, available: bool = True) -> None:
        self.responses = responses or {}
        self._available = available
        self.calls: List[str] = []
        self.last_search_urls: List[dict] = []
        self.blocked_domains: List[str] = []
        self.input_tokens = 0
        self.output_tokens = 0

    @property
    def available(self) -> bool:
        return self._available

    @property
    def unavailable_reason(self) -> Optional[str]:
        return None if self._available else "FakeLLM configured as unavailable"

    def structured(
        self, prompt: str, output_model: Type[BaseModel], *, label: str = "", **kwargs: Any
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        self.calls.append(label)
        if not self._available:
            return None, "unavailable"
        payload = self.responses.get(label)
        if payload is None:
            return None, f"no canned response for '{label}'"
        if isinstance(payload, str):
            return None, payload
        return output_model.model_validate(payload), None

    def usage_summary(self) -> str:
        return f"{len(self.calls)} fake call(s)."


@pytest.fixture
def fake_llm():
    return FakeLLM


# --------------------------------------------------------------------------
# Synthetic domain objects
# --------------------------------------------------------------------------


def deck_fact(value: str, locator: str = "Slide 1", confidence: float = 0.9) -> Fact:
    return Fact(
        value=value,
        evidence_type=EvidenceType.STATED_FACT,
        confidence=confidence,
        sources=[Evidence(source_type=SourceType.PITCH_DECK, locator=locator)],
    )


@pytest.fixture
def profile() -> CompanyProfile:
    p = CompanyProfile()
    p.company_name = deck_fact("Kestrel Authorization Systems")
    p.sector = deck_fact("Healthcare IT", "Slide 1")
    p.subsector = deck_fact("Prior authorization automation", "Slide 1")
    p.product_category = deck_fact("Clinic-side PA workflow automation", "Slide 4")
    p.core_product = deck_fact("Reads the chart and submits authorizations", "Slide 4")
    p.problem_addressed = deck_fact("13.7 hours per physician per week (AMA 2024)", "Slide 2")
    p.target_customer = deck_fact("Specialty clinics in ortho, GI, cardiology", "Slide 4")
    p.buyer = deck_fact("Practice administrators", "Slide 9")
    p.business_model = deck_fact("SaaS per provider plus usage", "Slide 6")
    p.pricing_model = deck_fact("Negotiated per practice", "Slide 6")
    p.distribution_model = deck_fact("Direct sales", "Slide 9")
    p.traction = Fact(
        value="142,000 authorizations; 91% first-pass approval",
        evidence_type=EvidenceType.COMPANY_CLAIM,
        confidence=0.7,
        sources=[Evidence(source_type=SourceType.PITCH_DECK, locator="Slide 5")],
    )
    p.key_ip = deck_fact("US patent application filed (pending)", "Slide 7")
    p.claimed_differentiation = Fact(
        value="KestrelPA is 10x faster than the manual process",
        evidence_type=EvidenceType.COMPANY_CLAIM,
        confidence=0.8,
        sources=[Evidence(source_type=SourceType.PITCH_DECK, locator="Slide 8")],
    )
    p.funding_stage = deck_fact("Seed", "Slide 1")
    p.known_competitors = deck_fact("Availity, Waystar", "Slide 8")
    return p


def make_competitor(
    name: str,
    category: CompetitorCategory = CompetitorCategory.DIRECT,
    threat: ThreatLevel = ThreatLevel.MEDIUM,
    relevance: float = 0.7,
    confidence: float = 0.6,
    named_in_deck: bool = False,
    **kwargs,
) -> Competitor:
    return Competitor(
        name=name,
        category=category,
        threat_level=threat,
        relevance=relevance,
        confidence=confidence,
        named_in_deck=named_in_deck,
        target_customer=kwargs.pop("target_customer", "Specialty clinics"),
        core_product=kwargs.pop("core_product", "Authorization automation"),
        distribution=kwargs.pop("distribution", "Direct sales"),
        strengths=kwargs.pop("strengths", ["Established payer connectivity"]),
        weaknesses=kwargs.pop("weaknesses", ["Shallow clinical workflow"]),
        **kwargs,
    )


@pytest.fixture
def competitors() -> List[Competitor]:
    return [
        make_competitor("Availity", CompetitorCategory.INCUMBENT, ThreatLevel.HIGH,
                        0.85, 0.8, named_in_deck=True),
        make_competitor("Humata Health", CompetitorCategory.DIRECT, ThreatLevel.HIGH, 0.9, 0.62),
        make_competitor("Status quo: in-house staff", CompetitorCategory.SUBSTITUTE,
                        ThreatLevel.HIGH, 0.95, 0.8),
        make_competitor("Rhyme", CompetitorCategory.INDIRECT, ThreatLevel.MEDIUM, 0.72, 0.68),
        make_competitor("Tennr", CompetitorCategory.EMERGING, ThreatLevel.MEDIUM, 0.55, 0.55),
        make_competitor("Waystar", CompetitorCategory.INCUMBENT, ThreatLevel.HIGH,
                        0.82, 0.8, named_in_deck=True),
    ]


@pytest.fixture
def dimensions() -> List[DimensionAssessment]:
    """A realistic mixed assessment: a couple of wins, several losses."""
    from config import ALL_DIMENSIONS

    out: List[DimensionAssessment] = []
    for i, name in enumerate(ALL_DIMENSIONS):
        if i % 6 == 0:
            out.append(
                DimensionAssessment(
                    dimension=name, advantage=Advantage.COMPANY,
                    company_position="Purpose-built exception queue",
                    competitor_position="Generic module",
                    evidence="Slide 4", evidence_type=EvidenceType.COMPANY_CLAIM,
                    confidence=0.5, sustainability="low",
                )
            )
        elif i % 3 == 0:
            out.append(
                DimensionAssessment(
                    dimension=name, advantage=Advantage.COMPETITOR,
                    company_position="No position established",
                    competitor_position="Owns the payer rail",
                    evidence="Slide 9", evidence_type=EvidenceType.STATED_FACT,
                    confidence=0.8, sustainability="high",
                )
            )
        elif i % 3 == 1:
            out.append(
                DimensionAssessment(
                    dimension=name, advantage=Advantage.NEUTRAL,
                    evidence="Comparable", evidence_type=EvidenceType.ANALYST_INFERENCE,
                    confidence=0.5, sustainability="medium",
                )
            )
        else:
            out.append(DimensionAssessment(dimension=name))
    return out


@pytest.fixture
def analysis(profile, competitors, dimensions) -> CompetitiveAnalysis:
    """A fully populated analysis, ready for the reporting layer."""
    from analysis.differentiation import compute_score

    diag = RunDiagnostics(llm_available=True, llm_calls=7, research_performed=True)
    score = compute_score(dimensions, profile, competitors, diag)

    return CompetitiveAnalysis(
        profile=profile,
        competitors=competitors,
        dimensions=dimensions,
        score=score,
        assessment=(
            "Kestrel has an early, self-reported outcome signal in a well-evidenced "
            "pain point, but every structural moat dimension currently favours "
            "incumbents or better-capitalised direct rivals."
        ),
        market_map=MarketMap(
            axis_x=MarketAxis(label="Automation depth", low_label="Human labor",
                              high_label="Autonomous AI"),
            axis_y=MarketAxis(label="Buyer served", low_label="Specialty clinic",
                              high_label="Health system"),
            entries=[
                MarketMapEntry(name="Kestrel", x=0.88, y=0.12, is_company=True),
                *[
                    MarketMapEntry(name=c.name, x=0.1 + 0.13 * i, y=0.2 + 0.1 * i,
                                   category=c.category)
                    for i, c in enumerate(competitors)
                ],
            ],
            rationale="Prior-auth players separate on automation depth and buyer served.",
        ),
        whitespace=[
            WhitespaceOpportunity(
                opportunity="Software-only tier for clinics under ten providers",
                evidence="Every listed competitor targets larger organisations",
                rationale="Existing rules corpus already covers these specialties",
                difficulty="medium",
                investor_relevance="Converts enterprise sales cycle into volume motion",
            ),
            WhitespaceOpportunity(
                opportunity="License the payer-rules corpus to specialty EHRs",
                evidence="Competitive set records shallow authorization modules",
                rationale="Monetises incumbent distribution instead of fighting it",
                difficulty="high",
                investor_relevance="Second revenue line; raises data-moat value",
            ),
            WhitespaceOpportunity(
                opportunity="FHIR-native client for CMS-0057-F payer APIs",
                evidence="Mandate effective Jan 2027; incumbents tied to legacy rails",
                rationale="Regulatory clock creates a dated buying trigger",
                difficulty="medium",
                investor_relevance="Time-boxed window ahead of incumbent response",
            ),
        ],
        concerns=[
            InvestorConcern(
                concern="EHR channel unsigned — is distribution the whole risk?",
                current_risk="Deck states nothing is signed; EHR incumbents own the chart data.",
                diligence_question="Which EHR vendors are in discussions, and at what stage?",
                evidence_needed="Signed or draft partnership term sheets and API agreements.",
                severity="high",
            ),
            InvestorConcern(
                concern="Can pricing hold against free connectivity?",
                current_risk="ACV sits against Availity's near-free provider connectivity.",
                diligence_question="In your last five competitive deals, what did rivals quote?",
                evidence_needed="Win/loss log naming competitor, quoted price and discount.",
                severity="high",
            ),
            InvestorConcern(
                concern="Is the performance claim verifiable?",
                current_risk="91% vs 79% is self-reported across four pilot sites.",
                diligence_question="Who audited the baseline, and what was the payer mix?",
                evidence_needed="Payer-attested approval rates with denominators.",
                severity="medium",
            ),
        ],
        positioning=Positioning(
            current_implied="Vague: a faster prior-auth tool claiming an uncited 10x speed-up.",
            recommended=(
                "For specialty clinics in orthopedics, GI and cardiology, KestrelPA is the "
                "prior-authorization layer that clears exception-queue cases EHR modules punt, "
                "unlike Availity, because specialty payer rules accumulate per submission."
            ),
            proof_points_required=[
                "Payer-attested first-pass approval rate with denominators",
                "Signed multi-year contracts showing per-provider ARR",
            ],
            claims_to_avoid=[
                '"10x faster than the manual process" — uncited superlative',
                '"Only solution purpose-built" — false given Humata and Infinx',
            ],
            competitive_narrative="Kestrel is betting the exception queue is a durable wedge.",
            word_count=33,
        ),
        consent=Consent().grant(public_research=True),
        diagnostics=diag,
    )
