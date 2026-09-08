"""
Competitor discovery (spec section 4).

The prompt is built around the spec's central analytical claim: competition is
for the same *customer, workflow and budget*, not the same sector. Two rules do
most of the work:

  * The deck's own competitor slide is an input, never the answer. Founders name
    flattering comparisons; the interesting competitors are the ones missing.
  * Substitutes -- manual process, internal build, outsourcing, doing nothing --
    are first-class competitors. For most early-stage B2B companies the status
    quo is the competitor that actually wins deals.

Discovery is separated from enrichment (`competitor_enrichment.py`) so that
*who competes* is decided on reasoning about the customer's alternatives, while
*facts about them* are gathered under strict no-fabrication rules.
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
    CompetitorCategory,
    Evidence,
    RunDiagnostics,
    SourceType,
    UNKNOWN,
)
from research.source_manager import SourceManager

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# LLM-facing schema
# --------------------------------------------------------------------------


class DiscoveredCompetitor(BaseModel):
    name: str = Field(description="Company or alternative name. For substitutes, name the workflow.")
    category: str = Field(description="One of: direct, indirect, incumbent, emerging, substitute.")
    why_competes: str = Field(
        description="The specific customer, workflow or budget contested. One sentence."
    )
    target_customer: str = Field(description="Who they sell to, or 'Unknown'.")
    core_product: str = Field(description="What they actually sell, or 'Unknown'.")
    positioning: str = Field(description="How they position themselves, or 'Unknown'.")
    business_model: str = Field(description="Their model, or 'Unknown'.")
    distribution: str = Field(description="How they reach customers, or 'Unknown'.")
    geographic_reach: str = Field(description="Where they operate, or 'Unknown'.")
    strengths: List[str] = Field(description="1-3 concrete strengths against this company.")
    weaknesses: List[str] = Field(description="1-3 concrete weaknesses this company can exploit.")
    defensibility: str = Field(description="What protects their position, or 'Unknown'.")
    threat_level: str = Field(description="high, medium or low, to THIS company specifically.")
    threat_rationale: str = Field(description="Why that threat level. One sentence.")
    relevance: float = Field(
        description="0.0-1.0: how directly they contest the same customer and budget."
    )
    confidence: float = Field(description="0.0-1.0 confidence that this is a real, relevant competitor.")
    named_in_deck: bool = Field(description="True only if the source material names them.")
    source: str = Field(
        description="'Slide N' if from the deck, a URL if from research, else 'market knowledge'."
    )


class DiscoveryResult(BaseModel):
    competitors: List[DiscoveredCompetitor]
    excluded: List[str] = Field(
        default_factory=list,
        description="Plausible-looking companies deliberately excluded, each with the reason.",
    )
    market_summary: str = Field(default="", description="Two sentences on the competitive structure.")


_DISCOVERY_RULES = """\
CATEGORY DEFINITIONS
- direct: substantially the same product to substantially the same customer.
- indirect: solves the same customer problem with a different approach.
- incumbent: already controls the customer relationship, distribution, infrastructure
  or budget line -- even if their product is worse. Their distribution is the threat.
- emerging: early-stage company or technology that could become material within
  the investment horizon.
- substitute: the alternative the customer uses today. Manual/in-house staff,
  outsourcing or BPO, spreadsheets and legacy systems, the incumbent EHR/ERP module,
  and doing nothing. At least one substitute MUST appear -- if the customer has no
  alternative to doing nothing, say which one they actually use today.

SELECTION RULES
1. Competition is for the same customer, the same workflow and the same budget.
   A company in the same sector that never appears in the same purchase decision
   is NOT a competitor. Exclude it and say why in `excluded`.
2. Do not simply repeat the deck's competitor slide. Use the company's problem,
   customer, buyer, workflow, product category, business model and geography to
   identify who else contests that budget. The competitors the deck omits are
   usually the ones that matter.
3. Name real, identifiable companies you are confident exist. If you are not
   confident a specific company exists and competes, describe the category of
   competitor instead of inventing a name, and lower confidence accordingly.
4. threat_level is relative to THIS company's specific position, not general size.
   A large incumbent that will never serve this customer segment is not a high threat.
5. Cover the range: aim for at least two direct or indirect, at least one incumbent,
   and at least one substitute.
"""


def _profile_brief(profile: CompanyProfile) -> str:
    """Compact, locator-annotated brief -- the discovery prompt's only company input."""
    interesting = [
        "company_name", "sector", "subsector", "product_category", "core_product",
        "problem_addressed", "target_customer", "buyer", "end_user", "primary_use_cases",
        "business_model", "pricing_model", "distribution_model", "geographic_markets",
        "customers", "revenue", "traction", "key_ip", "proprietary_data",
        "regulatory_advantages", "claimed_differentiation", "known_competitors",
        "funding_stage",
    ]
    lines = []
    for name in interesting:
        fact = profile.get(name)
        if fact.known:
            badge = fact.badge() or "?"
            lines.append(
                f"- {name.replace('_',' ')}: {fact.value} "
                f"[{badge}; {fact.evidence_type.value}; conf {fact.confidence:.2f}]"
            )
    missing = [m.replace("_", " ") for m in profile.missing_fields()]
    if missing:
        lines.append(f"- NOT ESTABLISHED from the material: {', '.join(missing[:14])}")
    return "\n".join(lines)


def discover(
    profile: CompanyProfile,
    client: LLMClient,
    sources: SourceManager,
    diagnostics: RunDiagnostics,
    settings: Optional[Settings] = None,
    extra_context: str = "",
) -> List[Competitor]:
    """Identify competitors across all five categories."""
    settings = settings or default_settings

    if not client.available:
        diagnostics.degraded = True
        diagnostics.warn(
            "Competitor discovery requires a language model. Without one the report can only "
            "list competitors named in the deck itself, which is not a competitive analysis."
        )
        return _from_deck_only(profile, diagnostics)

    use_search = sources.research_permitted
    prompt = (
        "Identify the competitive set for the company below.\n\n"
        f"{_DISCOVERY_RULES}\n"
        f"Return between {settings.min_competitors} and {settings.max_competitors} competitors, "
        "ordered by how much they threaten this company.\n\n"
        + (
            "You have web search available. Use it to confirm that the companies you name "
            "are real and currently active, and to find competitors you would otherwise miss. "
            "Cite the URL in `source` for anything you learned from search.\n\n"
            if use_search
            else "You do not have web search for this run. Rely on the supplied material and "
            "your established market knowledge; set `source` to 'market knowledge' and keep "
            "confidence below 0.7 for anything not in the material.\n\n"
        )
        + f"===== COMPANY PROFILE =====\n{_profile_brief(profile)}\n"
        + (f"\n===== ADDITIONAL SOURCES =====\n{extra_context[:20000]}\n" if extra_context else "")
        + "===== END ====="
    )

    result, error = client.structured(
        prompt,
        DiscoveryResult,
        label="competitor-discovery",
        max_tokens=24000,
        web_search=use_search,
        allowed_domains=sources.allowed_domains if use_search else None,
    )

    if use_search:
        sources.record_search_urls(client.last_search_urls)

    if error or result is None:
        diagnostics.warn(f"Competitor discovery failed: {error}")
        diagnostics.degraded = True
        return _from_deck_only(profile, diagnostics)

    diagnostics.llm_calls += 1
    competitors = [_to_competitor(d) for d in result.competitors]

    if result.excluded:
        diagnostics.note(
            "Deliberately excluded as non-competing: " + "; ".join(result.excluded[:5])
        )
    if result.market_summary:
        diagnostics.note(f"Market structure: {result.market_summary}")

    logger.info("Discovered %d competitors", len(competitors))
    return competitors


def _to_competitor(d: DiscoveredCompetitor) -> Competitor:
    from models.schemas import ThreatLevel

    from extraction.evidence import build_evidence

    try:
        category = CompetitorCategory(d.category.strip().lower())
    except ValueError:
        category = CompetitorCategory.DIRECT
    try:
        threat = ThreatLevel(d.threat_level.strip().lower())
    except ValueError:
        threat = ThreatLevel.MEDIUM

    url = d.source if d.source.startswith("http") else None
    evidence = build_evidence(
        locator=d.source if not url else None,
        source_type="pitch_deck" if d.named_in_deck else ("public_source" if url else "analyst_inference"),
        excerpt=d.why_competes,
        url=url,
    )

    return Competitor(
        name=d.name.strip(),
        website=url,
        category=category,
        target_customer=d.target_customer or UNKNOWN,
        core_product=d.core_product or UNKNOWN,
        positioning=d.positioning or UNKNOWN,
        primary_use_case=d.why_competes or UNKNOWN,
        business_model=d.business_model or UNKNOWN,
        distribution=d.distribution or UNKNOWN,
        geographic_reach=d.geographic_reach or UNKNOWN,
        strengths=[s for s in d.strengths if s][:3],
        weaknesses=[w for w in d.weaknesses if w][:3],
        defensibility=d.defensibility or UNKNOWN,
        threat_level=threat,
        threat_rationale=d.threat_rationale,
        relevance=d.relevance,
        confidence=d.confidence,
        sources=[evidence],
        named_in_deck=d.named_in_deck,
    )


def _from_deck_only(profile: CompanyProfile, diagnostics: RunDiagnostics) -> List[Competitor]:
    """Degraded path: split the deck's own competitor field into records.

    This is explicitly *not* a competitive analysis, and the report says so.
    """
    raw = profile.known_competitors
    if not raw.known:
        diagnostics.warn("No competitors could be identified and the deck names none.")
        return []

    names = _plausible_names(raw.value or "")
    if not names:
        diagnostics.warn(
            "The deck's competitor text could not be parsed into company names without a "
            "language model, so no competitor records were created. The competitive "
            "analysis in this report is empty."
        )
        return []

    competitors: List[Competitor] = []
    for name in names[:6]:
        competitors.append(
            Competitor(
                name=name,
                category=CompetitorCategory.DIRECT,
                relevance=0.5,
                confidence=0.3,
                named_in_deck=True,
                threat_rationale="Named by the company; not independently assessed.",
                sources=list(raw.sources) or [Evidence(source_type=SourceType.PITCH_DECK)],
            )
        )
    diagnostics.warn(
        f"Listing only the {len(competitors)} competitor(s) named in the deck. "
        "No independent discovery was performed."
    )
    return competitors


#: Words that mark a fragment as prose rather than a company name.
_PROSE_MARKERS = frozenset(
    "the a an is are was were we our us they their it its this that these those "
    "than then with without from into onto for and or but not only just more most "
    "faster better cheaper easier adopt adopted solution solutions platform focused "
    "purpose built rather instead because which who what when where how".split()
)


def _plausible_names(raw: str) -> List[str]:
    """Pull competitor names out of a free-text field, conservatively.

    The heuristic extractor hands over whole slide bodies, so naive splitting
    produces fragments like "Waystar) — transaction plumbing". A fragment is
    accepted only if it reads like a proper noun: at most four words, no
    sentence vocabulary, and starting with a capital. Recovering nothing is a
    better outcome than putting a sentence fragment in a competitor column.
    """
    import re

    # Bracketed lists ("clearinghouses (Availity, Waystar)") are the common
    # shape, and splitting around them leaves unbalanced fragments. Treat the
    # brackets themselves as separators.
    raw = raw.replace("(", ", ").replace(")", ", ")

    candidates: List[str] = []
    for chunk in re.split(r"[,;/\n]|\band\b|\bvs\.?\b|\|", raw):
        name = chunk.strip(" .;:—-()[]\t")
        # Trim a trailing explanatory clause: "Availity — transaction plumbing".
        name = re.split(r"\s[—–-]\s", name)[0].strip()
        if not (2 < len(name) <= 40):
            continue
        words = name.split()
        if not (1 <= len(words) <= 4):
            continue
        if not name[0].isupper():
            continue
        if any(w.lower().strip(".,") in _PROSE_MARKERS for w in words):
            continue
        if name not in candidates:
            candidates.append(name)
    return candidates
