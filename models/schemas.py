"""
Core data model for the Competitive Intelligence one-pager generator.

ARCHITECTURAL DECISION -- "evidence-first" modelling
----------------------------------------------------
Every non-trivial assertion the system makes is wrapped in a `Fact`, which
carries not just a value but *where it came from* (`sources`), *what kind of
claim it is* (`evidence_type`) and *how sure we are* (`confidence`).

That is deliberate: the primary failure mode of an LLM-backed diligence tool is
laundering a company's marketing claim, or the model's own guess, into something
that looks like an established fact on an investor-facing page. Making the
provenance part of the type -- rather than a convention -- means the analysis
and reporting layers physically cannot render a value without having had the
opportunity to render its provenance, and the scoring layer can (and does)
discount unevidenced claims.

`Unknown` / `Not publicly disclosed` are first-class values. Absence of data is
information; it is never silently filled in.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------
# Sentinels
# --------------------------------------------------------------------------

UNKNOWN = "Unknown"
NOT_DISCLOSED = "Not publicly disclosed"

#: Values that mean "we do not know", however the model chose to phrase it.
_NULLISH = {
    "",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "not disclosed",
    "not publicly disclosed",
    "not stated",
    "not specified",
    "not provided",
    "not available",
    "undisclosed",
    "tbd",
    "-",
    "--",
}


def is_nullish(value: Optional[str]) -> bool:
    """True when a string carries no actual information."""
    if value is None:
        return True
    return str(value).strip().lower() in _NULLISH


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


class SourceType(str, Enum):
    """*Where* a piece of information physically came from."""

    PITCH_DECK = "PITCH_DECK"
    UPLOADED_DOCUMENT = "UPLOADED_DOCUMENT"
    COMPANY_WEBSITE = "COMPANY_WEBSITE"
    PUBLIC_SOURCE = "PUBLIC_SOURCE"
    USER_PROVIDED = "USER_PROVIDED"
    ANALYST_INFERENCE = "ANALYST_INFERENCE"
    UNKNOWN = "UNKNOWN"


class EvidenceType(str, Enum):
    """*What kind of claim* a piece of information is.

    This is the epistemic axis, orthogonal to `SourceType`. A number printed on
    a pitch deck slide is sourced from PITCH_DECK but is only a COMPANY_CLAIM
    until corroborated -- the distinction the spec requires us to preserve.
    """

    STATED_FACT = "stated_fact"              # verifiable, attributable statement
    COMPANY_CLAIM = "company_claim"          # asserted by the company, uncorroborated
    ANALYST_INFERENCE = "analyst_inference"  # derived by this system
    UNVERIFIED = "unverified"                # present but no usable provenance
    MISSING = "missing"                      # not found anywhere


#: Short tokens used in the PDF's compact source indicators.
SOURCE_BADGE = {
    SourceType.PITCH_DECK: "DECK",
    SourceType.UPLOADED_DOCUMENT: "DOC",
    SourceType.COMPANY_WEBSITE: "WEB",
    SourceType.PUBLIC_SOURCE: "PUB",
    SourceType.USER_PROVIDED: "USER",
    SourceType.ANALYST_INFERENCE: "INF",
    SourceType.UNKNOWN: "?",
}


class Evidence(BaseModel):
    """A single traceable citation."""

    source_type: SourceType = SourceType.UNKNOWN
    locator: str = ""                 # "Slide 6", "Page 3", document name
    excerpt: str = ""                 # short verbatim support, kept for audit
    url: Optional[str] = None

    def badge(self) -> str:
        """Compact indicator for the one-pager, e.g. ``DECK s6``."""
        tag = SOURCE_BADGE.get(self.source_type, "?")
        loc = (self.locator or "").strip()
        if not loc:
            return tag
        short = (
            loc.replace("Slide ", "s")
            .replace("slide ", "s")
            .replace("Page ", "p")
            .replace("page ", "p")
        )
        return f"{tag} {short}"[:22]


class Fact(BaseModel):
    """A value plus its provenance. The unit of currency in this system."""

    value: Optional[str] = None
    evidence_type: EvidenceType = EvidenceType.MISSING
    confidence: float = 0.0
    sources: List[Evidence] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @property
    def known(self) -> bool:
        return not is_nullish(self.value)

    def display(self, fallback: str = UNKNOWN) -> str:
        return self.value.strip() if self.known else fallback

    def badge(self) -> str:
        return self.sources[0].badge() if self.sources else ""

    @classmethod
    def missing(cls) -> "Fact":
        return cls()

    @classmethod
    def from_user(cls, value: Optional[str]) -> "Fact":
        """User-supplied form input: treated as a stated fact at high confidence."""
        if is_nullish(value):
            return cls.missing()
        return cls(
            value=str(value).strip(),
            evidence_type=EvidenceType.STATED_FACT,
            confidence=0.9,
            sources=[Evidence(source_type=SourceType.USER_PROVIDED, locator="User input")],
        )


# --------------------------------------------------------------------------
# Ingestion model -- the common shape every reader normalizes into
# --------------------------------------------------------------------------

BlockKind = Literal["title", "heading", "body", "table", "notes", "metadata"]


class DocumentBlock(BaseModel):
    """One addressable chunk of a source document."""

    kind: BlockKind = "body"
    text: str = ""
    index: int = 0             # 1-based slide / page number
    locator: str = ""          # human-readable, e.g. "Slide 4"

    def render(self) -> str:
        prefix = {
            "title": "# ",
            "heading": "## ",
            "notes": "[notes] ",
            "table": "[table] ",
        }.get(self.kind, "")
        return f"{prefix}{self.text}".strip()


class SourceDocument(BaseModel):
    """A pitch deck or supporting document, normalized."""

    name: str
    path: Optional[str] = None
    doc_type: Literal["pdf", "pptx", "text", "url"] = "pdf"
    source_type: SourceType = SourceType.PITCH_DECK
    blocks: List[DocumentBlock] = Field(default_factory=list)
    unit_count: int = 0                       # pages or slides
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def char_count(self) -> int:
        """Extracted *content* length.

        File metadata is excluded deliberately: an image-only deck that happens
        to carry a PDF Title would otherwise report a non-zero character count
        and slip past the "no extractable text" check, producing a confident
        report built on nothing.
        """
        return sum(len(b.text) for b in self.blocks if b.kind != "metadata")

    @property
    def text_bearing_units(self) -> int:
        return len({b.index for b in self.blocks if b.text.strip()})

    def as_text(self, max_chars: Optional[int] = None) -> str:
        """Flatten to a locator-annotated transcript for the extraction prompt."""
        out: List[str] = []
        current = None
        for b in self.blocks:
            if not b.text.strip():
                continue
            if b.index != current:
                current = b.index
                out.append("\n--- " + (b.locator or f"Unit {b.index}") + " ---")
            out.append(b.render())
        text = "\n".join(out).strip()
        if max_chars is not None and len(text) > max_chars:
            # Truncation is a reportable event, never silent -- callers record it.
            text = text[:max_chars] + "\n[... transcript truncated ...]"
        return text


class IngestionResult(BaseModel):
    """Everything the ingestion phase produced, including its complaints."""

    primary: Optional[SourceDocument] = None
    supporting: List[SourceDocument] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.primary is not None and self.primary.char_count > 0

    def all_documents(self) -> List[SourceDocument]:
        return ([self.primary] if self.primary else []) + list(self.supporting)


# --------------------------------------------------------------------------
# Company profile
# --------------------------------------------------------------------------

#: Canonical profile field order. Drives the LLM's allowed field names, the
#: heuristic extractor, and the JSON record -- one list, no drift.
PROFILE_FIELDS: List[str] = [
    "company_name",
    "website",
    "headquarters",
    "sector",
    "subsector",
    "product_category",
    "core_product",
    "problem_addressed",
    "target_customer",
    "buyer",
    "end_user",
    "primary_use_cases",
    "business_model",
    "pricing_model",
    "revenue",
    "revenue_growth",
    "customers",
    "users",
    "partnerships",
    "distribution_model",
    "geographic_markets",
    "funding_raised",
    "current_round",
    "valuation",
    "key_ip",
    "regulatory_advantages",
    "proprietary_data",
    "network_effects",
    "switching_costs",
    "claimed_differentiation",
    "funding_stage",
    "raise_amount",
    "traction",
    "known_competitors",
]


class CompanyProfile(BaseModel):
    """Normalized company facts. Every field is a `Fact`, so every field is auditable."""

    company_name: Fact = Field(default_factory=Fact.missing)
    website: Fact = Field(default_factory=Fact.missing)
    headquarters: Fact = Field(default_factory=Fact.missing)
    sector: Fact = Field(default_factory=Fact.missing)
    subsector: Fact = Field(default_factory=Fact.missing)
    product_category: Fact = Field(default_factory=Fact.missing)
    core_product: Fact = Field(default_factory=Fact.missing)
    problem_addressed: Fact = Field(default_factory=Fact.missing)
    target_customer: Fact = Field(default_factory=Fact.missing)
    buyer: Fact = Field(default_factory=Fact.missing)
    end_user: Fact = Field(default_factory=Fact.missing)
    primary_use_cases: Fact = Field(default_factory=Fact.missing)
    business_model: Fact = Field(default_factory=Fact.missing)
    pricing_model: Fact = Field(default_factory=Fact.missing)
    revenue: Fact = Field(default_factory=Fact.missing)
    revenue_growth: Fact = Field(default_factory=Fact.missing)
    customers: Fact = Field(default_factory=Fact.missing)
    users: Fact = Field(default_factory=Fact.missing)
    partnerships: Fact = Field(default_factory=Fact.missing)
    distribution_model: Fact = Field(default_factory=Fact.missing)
    geographic_markets: Fact = Field(default_factory=Fact.missing)
    funding_raised: Fact = Field(default_factory=Fact.missing)
    current_round: Fact = Field(default_factory=Fact.missing)
    valuation: Fact = Field(default_factory=Fact.missing)
    key_ip: Fact = Field(default_factory=Fact.missing)
    regulatory_advantages: Fact = Field(default_factory=Fact.missing)
    proprietary_data: Fact = Field(default_factory=Fact.missing)
    network_effects: Fact = Field(default_factory=Fact.missing)
    switching_costs: Fact = Field(default_factory=Fact.missing)
    claimed_differentiation: Fact = Field(default_factory=Fact.missing)
    funding_stage: Fact = Field(default_factory=Fact.missing)
    raise_amount: Fact = Field(default_factory=Fact.missing)
    traction: Fact = Field(default_factory=Fact.missing)
    known_competitors: Fact = Field(default_factory=Fact.missing)

    extraction_notes: List[str] = Field(default_factory=list)

    def get(self, name: str) -> Fact:
        return getattr(self, name, Fact.missing())

    def known_fields(self) -> List[str]:
        return [f for f in PROFILE_FIELDS if self.get(f).known]

    def missing_fields(self) -> List[str]:
        return [f for f in PROFILE_FIELDS if not self.get(f).known]

    @property
    def coverage(self) -> float:
        """Share of profile fields with usable values -- an input to confidence."""
        return len(self.known_fields()) / len(PROFILE_FIELDS)

    def display_name(self) -> str:
        return self.company_name.display("The Company")


# --------------------------------------------------------------------------
# Competitors
# --------------------------------------------------------------------------


class CompetitorCategory(str, Enum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    INCUMBENT = "incumbent"
    EMERGING = "emerging"
    SUBSTITUTE = "substitute"


CATEGORY_LABEL = {
    CompetitorCategory.DIRECT: "Direct",
    CompetitorCategory.INDIRECT: "Indirect",
    CompetitorCategory.INCUMBENT: "Incumbent",
    CompetitorCategory.EMERGING: "Emerging",
    CompetitorCategory.SUBSTITUTE: "Substitute",
}


class ThreatLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


THREAT_RANK = {ThreatLevel.HIGH: 3, ThreatLevel.MEDIUM: 2, ThreatLevel.LOW: 1}


class Competitor(BaseModel):
    """A competing option for the same customer, workflow, or budget.

    Financial and operating fields default to `Not publicly disclosed` rather
    than to an estimate. Nothing in the pipeline is permitted to fill them in
    by inference -- see `research/competitor_enrichment.py`.
    """

    name: str
    website: Optional[str] = None
    category: CompetitorCategory = CompetitorCategory.DIRECT
    headquarters: str = UNKNOWN
    founded_year: str = UNKNOWN
    target_customer: str = UNKNOWN
    core_product: str = UNKNOWN
    positioning: str = UNKNOWN
    primary_use_case: str = UNKNOWN
    business_model: str = UNKNOWN
    pricing: str = NOT_DISCLOSED
    revenue: str = NOT_DISCLOSED
    traction: str = NOT_DISCLOSED
    customer_count: str = NOT_DISCLOSED
    user_count: str = NOT_DISCLOSED
    funding_raised: str = NOT_DISCLOSED
    latest_round: str = NOT_DISCLOSED
    valuation: str = NOT_DISCLOSED
    investors: List[str] = Field(default_factory=list)
    distribution: str = UNKNOWN
    geographic_reach: str = UNKNOWN
    partnerships: str = NOT_DISCLOSED
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    defensibility: str = UNKNOWN
    threat_level: ThreatLevel = ThreatLevel.MEDIUM
    threat_rationale: str = ""
    relevance: float = 0.5            # how much this competes for the same budget
    confidence: float = 0.4           # data quality for this record
    sources: List[Evidence] = Field(default_factory=list)
    named_in_deck: bool = False

    @field_validator("relevance", "confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @property
    def priority(self) -> float:
        """Ranking key for the matrix: threat x relevance, evidence-weighted."""
        return (
            THREAT_RANK[self.threat_level] * 0.45
            + self.relevance * 0.40
            + self.confidence * 0.15
        )

    def badge(self) -> str:
        if self.sources:
            return self.sources[0].badge()
        return SOURCE_BADGE[SourceType.ANALYST_INFERENCE]


# --------------------------------------------------------------------------
# Analysis outputs
# --------------------------------------------------------------------------


class Advantage(str, Enum):
    COMPANY = "company"
    COMPETITOR = "competitor"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class DimensionAssessment(BaseModel):
    """One row of the 18-dimension competitive assessment (spec section 6)."""

    dimension: str
    advantage: Advantage = Advantage.UNKNOWN
    company_position: str = UNKNOWN
    competitor_position: str = UNKNOWN
    evidence: str = ""
    evidence_type: EvidenceType = EvidenceType.MISSING
    confidence: float = 0.0
    sustainability: Literal["high", "medium", "low", "unknown"] = "unknown"

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


class CategoryScore(BaseModel):
    name: str
    weight: int
    raw: float                 # 0..1 achievement within the category
    weighted: float            # raw * weight
    rationale: str = ""
    evidence_backed: bool = False


class DifferentiationScore(BaseModel):
    total: int = 0
    confidence: float = 0.0
    categories: List[CategoryScore] = Field(default_factory=list)
    band: str = "Weak differentiation"
    explanation: str = ""
    unevidenced_claims_discounted: List[str] = Field(default_factory=list)


class MarketAxis(BaseModel):
    label: str
    low_label: str
    high_label: str


class MarketMapEntry(BaseModel):
    name: str
    x: float                   # 0..1 along axis_x
    y: float                   # 0..1 along axis_y
    is_company: bool = False
    category: CompetitorCategory = CompetitorCategory.DIRECT

    @field_validator("x", "y")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


class MarketMap(BaseModel):
    axis_x: MarketAxis
    axis_y: MarketAxis
    entries: List[MarketMapEntry] = Field(default_factory=list)
    rationale: str = ""


class WhitespaceOpportunity(BaseModel):
    opportunity: str
    evidence: str = ""
    rationale: str = ""
    difficulty: Literal["low", "medium", "high"] = "medium"
    investor_relevance: str = ""
    evidence_type: EvidenceType = EvidenceType.ANALYST_INFERENCE


class InvestorConcern(BaseModel):
    """Deliberately three separate fields -- the spec requires the current risk,
    the question to ask, and the evidence that would settle it to stay distinct."""

    concern: str
    current_risk: str
    diligence_question: str
    evidence_needed: str
    severity: Literal["high", "medium", "low"] = "medium"


class Positioning(BaseModel):
    current_implied: str = UNKNOWN
    recommended: str = ""
    proof_points_required: List[str] = Field(default_factory=list)
    claims_to_avoid: List[str] = Field(default_factory=list)
    competitive_narrative: str = ""
    word_count: int = 0


class MatrixRow(BaseModel):
    company: str
    customer: str = UNKNOWN
    product: str = UNKNOWN
    pricing: str = NOT_DISCLOSED
    traction: str = NOT_DISCLOSED
    distribution: str = UNKNOWN
    advantage: str = UNKNOWN
    threat: str = "-"
    is_subject: bool = False


class CompetitiveMatrix(BaseModel):
    rows: List[MatrixRow] = Field(default_factory=list)
    note: str = ""


# --------------------------------------------------------------------------
# Run configuration & consent
# --------------------------------------------------------------------------


class Consent(BaseModel):
    status: Literal["granted", "declined", "not_provided"] = "not_provided"
    public_research_permitted: bool = False
    external_transmission_permitted: bool = False
    timestamp: Optional[str] = None

    def grant(self, public_research: bool = True) -> "Consent":
        self.status = "granted"
        self.public_research_permitted = public_research
        self.timestamp = utc_now_iso()
        return self


class UserInputs(BaseModel):
    """Optional operator-supplied context (spec section 1)."""

    user_name: Optional[str] = None
    user_email: Optional[str] = None
    company_name: Optional[str] = None
    website: Optional[str] = None
    sector: Optional[str] = None
    subsector: Optional[str] = None
    product_description: Optional[str] = None
    target_customers: Optional[str] = None
    geography: Optional[str] = None
    business_model: Optional[str] = None
    pricing: Optional[str] = None
    known_competitors: Optional[str] = None
    funding_stage: Optional[str] = None
    raise_amount: Optional[str] = None
    traction: Optional[str] = None
    notes: Optional[str] = None
    source_urls: List[str] = Field(default_factory=list)

    def as_profile_overrides(self) -> Dict[str, Fact]:
        """Map the free-form form onto canonical profile fields."""
        mapping = {
            "company_name": self.company_name,
            "website": self.website,
            "sector": self.sector,
            "subsector": self.subsector,
            "core_product": self.product_description,
            "target_customer": self.target_customers,
            "geographic_markets": self.geography,
            "business_model": self.business_model,
            "pricing_model": self.pricing,
            "known_competitors": self.known_competitors,
            "funding_stage": self.funding_stage,
            "raise_amount": self.raise_amount,
            "traction": self.traction,
        }
        return {k: Fact.from_user(v) for k, v in mapping.items() if not is_nullish(v)}


# --------------------------------------------------------------------------
# The complete analysis
# --------------------------------------------------------------------------


class RunDiagnostics(BaseModel):
    """Nothing fails silently: every degradation lands here and is surfaced."""

    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    info: List[str] = Field(default_factory=list)
    llm_available: bool = False
    llm_calls: int = 0
    research_performed: bool = False
    degraded: bool = False
    pdf_reduction_steps: List[str] = Field(default_factory=list)

    def error(self, msg: str) -> None:
        if msg not in self.errors:
            self.errors.append(msg)

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def note(self, msg: str) -> None:
        if msg not in self.info:
            self.info.append(msg)


class CompetitiveAnalysis(BaseModel):
    """The single object the reporting layer consumes."""

    profile: CompanyProfile = Field(default_factory=CompanyProfile)
    competitors: List[Competitor] = Field(default_factory=list)
    dimensions: List[DimensionAssessment] = Field(default_factory=list)
    score: DifferentiationScore = Field(default_factory=DifferentiationScore)
    matrix: CompetitiveMatrix = Field(default_factory=CompetitiveMatrix)
    market_map: Optional[MarketMap] = None
    whitespace: List[WhitespaceOpportunity] = Field(default_factory=list)
    concerns: List[InvestorConcern] = Field(default_factory=list)
    positioning: Positioning = Field(default_factory=Positioning)
    assessment: str = ""
    consent: Consent = Field(default_factory=Consent)
    user: UserInputs = Field(default_factory=UserInputs)
    diagnostics: RunDiagnostics = Field(default_factory=RunDiagnostics)
    analysis_timestamp: str = Field(default_factory=utc_now_iso)

    def selected_competitors(self, limit: int = 6) -> List[Competitor]:
        return sorted(self.competitors, key=lambda c: c.priority, reverse=True)[:limit]
