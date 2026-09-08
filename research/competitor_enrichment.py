"""
Competitor enrichment (spec section 5).

Discovery establishes *who* competes; this module fills in *what is known about
them*. The two are separated because they warrant different epistemics:
reasoning about who contests a budget is legitimate analyst work, whereas
asserting a competitor's revenue is a factual claim that must be sourced or
withheld.

The no-fabrication rule is enforced three ways, not one:
  1. the prompt states it explicitly and gives the exact sentinel strings;
  2. the response schema uses required strings so "" cannot pass as a value;
  3. `_sanitise` rejects hedged values ("approximately $50M", "~200 customers")
     for financial fields after the fact, because a hedged invention is still
     an invention.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from pydantic import BaseModel, Field

from config import Settings, settings as default_settings
from llm.client import LLMClient
from models.schemas import (
    NOT_DISCLOSED,
    UNKNOWN,
    CompanyProfile,
    Competitor,
    RunDiagnostics,
    is_nullish,
)
from research.source_manager import SourceManager

logger = logging.getLogger(__name__)


class EnrichedCompetitor(BaseModel):
    name: str = Field(description="Must match the supplied competitor name exactly.")
    website: str = Field(description="Official URL, or 'Unknown'.")
    headquarters: str = Field(description="City, country, or 'Unknown'.")
    founded_year: str = Field(description="Year founded, or 'Unknown'.")
    pricing: str = Field(description="Pricing or pricing model, or 'Not publicly disclosed'.")
    revenue: str = Field(description="Revenue, or 'Not publicly disclosed'.")
    traction: str = Field(description="Traction indicators, or 'Not publicly disclosed'.")
    customer_count: str = Field(description="Customer count, or 'Not publicly disclosed'.")
    user_count: str = Field(description="User count, or 'Not publicly disclosed'.")
    funding_raised: str = Field(description="Total raised, or 'Not publicly disclosed'.")
    latest_round: str = Field(description="Most recent round and date, or 'Not publicly disclosed'.")
    valuation: str = Field(description="Valuation, or 'Not publicly disclosed'.")
    investors: List[str] = Field(default_factory=list, description="Named investors only; empty if none known.")
    partnerships: str = Field(description="Notable partnerships, or 'Not publicly disclosed'.")
    source_urls: List[str] = Field(
        default_factory=list, description="URLs supporting any non-sentinel value above."
    )
    confidence: float = Field(description="0.0-1.0 confidence in this record's factual content.")


class EnrichmentResult(BaseModel):
    competitors: List[EnrichedCompetitor]
    unresolved: List[str] = Field(
        default_factory=list, description="Competitors for which nothing reliable was found."
    )


#: Fields where an unsupported number is a serious defect, so hedged or
#: rounded-looking values are rejected rather than displayed.
_FINANCIAL_FIELDS = {
    "pricing", "revenue", "customer_count", "user_count",
    "funding_raised", "latest_round", "valuation",
}

_HEDGE_RE = re.compile(
    r"(\b(approx\w*|around|about|roughly|estimat\w+|est\.|likely|probably|possibly|"
    r"may be|could be|report(ed|edly|s)?|rumou?red|believ\w+|circa|or so|"
    r"in the range of|somewhere|unconfirmed|allegedly|thought to)\b|~)",
    re.IGNORECASE,
)


#: Internal vocabulary the model sometimes echoes back into a value. It is
#: meaningful to the pipeline but meaningless — and unprofessional — on an
#: investor page, so it is stripped wherever it appears.
_JARGON_RE = re.compile(
    r"\b(company[_ ]claim|stated[_ ]fact|analyst[_ ]inference|unverified|evidence[_ ]type)\b"
    r"\s*[:\-—]?\s*",
    re.IGNORECASE,
)


def _sanitise(value: Optional[str], field: str) -> str:
    """Normalise a returned value, replacing anything unsupportable with a sentinel."""
    default = NOT_DISCLOSED if field in _FINANCIAL_FIELDS else UNKNOWN
    if is_nullish(value):
        return default
    text = _JARGON_RE.sub("", str(value)).strip(" :—-").strip()
    if is_nullish(text):
        return default
    if field in _FINANCIAL_FIELDS and _HEDGE_RE.search(text):
        # A hedged financial figure is a guess wearing a hedge. Drop it.
        return default
    return text[:120]


def enrich(
    competitors: List[Competitor],
    profile: CompanyProfile,
    client: LLMClient,
    sources: SourceManager,
    diagnostics: RunDiagnostics,
    settings: Optional[Settings] = None,
) -> List[Competitor]:
    """Fill factual fields on discovered competitors. Returns them either way."""
    settings = settings or default_settings

    if not competitors:
        return competitors
    if not client.available:
        diagnostics.note(
            "Competitor factual detail was not gathered (no language model available); "
            "financial and traction fields remain undisclosed."
        )
        return competitors

    use_search = sources.research_permitted
    if not use_search:
        diagnostics.note(
            "Competitor enrichment ran without public research. Financial and traction "
            "fields are reported as undisclosed unless already established."
        )

    listing = "\n".join(
        f"- {c.name} [{c.category.value}] — {c.core_product[:90]}" for c in competitors
    )

    prompt = (
        "Gather verifiable factual detail on each competitor listed below. This supports "
        "an investment diligence report, so an unsupported number is worse than no number.\n\n"
        "ABSOLUTE RULES\n"
        "- Never invent or estimate revenue, funding, pricing, customer counts, user counts, "
        "valuations, market share or partnerships.\n"
        "- If a value is not established, output exactly 'Not publicly disclosed'.\n"
        "- For qualitative fields that are unknown, output exactly 'Unknown'.\n"
        "- Do not hedge ('approximately', 'around', 'reportedly'). Either you have the figure "
        "from a source, or you output the sentinel.\n"
        "- List investors only where you can name them specifically.\n"
        "- For substitutes and generic alternatives (in-house staffing, outsourcing, "
        "doing nothing), most fields will legitimately be sentinels. That is correct.\n\n"
        + (
            "Use web search to confirm values and record the supporting URL in source_urls. "
            "A value with no source_urls entry must be one you are confident of from "
            "well-established public record; otherwise use the sentinel.\n\n"
            if use_search
            else "You have no web search for this run. Report only values you are confident of "
            "from well-established public record; use the sentinels for everything else.\n\n"
        )
        + f"Subject company for context: {profile.display_name()} — "
        f"{profile.core_product.display('product not established')} "
        f"targeting {profile.target_customer.display('customer not established')}.\n\n"
        f"===== COMPETITORS =====\n{listing}\n===== END ====="
    )

    result, error = client.structured(
        prompt,
        EnrichmentResult,
        label="competitor-enrichment",
        max_tokens=20000,
        web_search=use_search,
        allowed_domains=sources.allowed_domains if use_search else None,
        max_searches=10,
    )

    if use_search:
        sources.record_search_urls(client.last_search_urls)

    if error or result is None:
        diagnostics.warn(
            f"Competitor enrichment failed: {error} "
            "Competitor financial and traction fields remain undisclosed."
        )
        return competitors

    diagnostics.llm_calls += 1

    from analysis.competitor_classifier import canonical
    from models.schemas import Evidence, SourceType

    by_key = {canonical(c.name): c for c in competitors}
    enriched_count = 0
    unmatched: List[str] = []

    for item in result.competitors:
        target = _match(item.name, by_key)
        if target is None:
            # A dropped record is a silent quality loss; record it instead.
            unmatched.append(item.name)
            continue

        for field in (
            "website", "headquarters", "founded_year", "pricing", "revenue", "traction",
            "customer_count", "user_count", "funding_raised", "latest_round", "valuation",
            "partnerships",
        ):
            value = _sanitise(getattr(item, field, None), field)
            current = getattr(target, field, None)
            # Only overwrite a sentinel; discovery's qualitative read is not
            # displaced by enrichment.
            if current in (None, UNKNOWN, NOT_DISCLOSED) or is_nullish(current):
                setattr(target, field, value)

        if item.investors:
            target.investors = [i.strip() for i in item.investors if i and not is_nullish(i)][:5]

        for url in item.source_urls[:3]:
            if url.startswith("http"):
                evidence = Evidence(source_type=SourceType.PUBLIC_SOURCE, locator=url[:60], url=url)
                target.sources.append(evidence)
                sources.record(evidence)

        # Confidence only ever rises on corroboration, and only modestly.
        target.confidence = max(target.confidence, min(item.confidence, 0.9))
        enriched_count += 1

    if result.unresolved:
        diagnostics.note(
            "No reliable public detail found for: " + ", ".join(result.unresolved[:5])
        )
    if unmatched:
        diagnostics.warn(
            f"{len(unmatched)} enrichment record(s) could not be matched to a discovered "
            "competitor and were discarded: " + ", ".join(unmatched[:4])
        )
    if enriched_count == 0 and competitors:
        diagnostics.warn(
            "No competitor factual detail could be applied. Competitor financial, "
            "pricing and traction fields are reported as undisclosed."
        )

    logger.info("Enriched %d/%d competitors", enriched_count, len(competitors))
    diagnostics.note(f"Factual detail gathered for {enriched_count} of {len(competitors)} competitors.")
    return competitors


def _match(name: str, by_key: dict):
    """Resolve a returned competitor name back to a discovered record.

    Exact canonical match first. Failing that, fall back to containment in
    either direction: models routinely shorten a long descriptive name
    ("Outsourced RCM / prior-auth BPO (e.g. AGS Health)" -> "Outsourced RCM
    BPO"), and dropping a whole enrichment record over a shortened label loses
    real, sourced data for no good reason.
    """
    from analysis.competitor_classifier import canonical

    key = canonical(name)
    if not key:
        return None
    if key in by_key:
        return by_key[key]

    candidates = [
        target
        for existing, target in by_key.items()
        if len(existing) >= 5 and len(key) >= 5 and (existing in key or key in existing)
    ]
    # Only accept an unambiguous fallback; two matches means we cannot tell.
    return candidates[0] if len(candidates) == 1 else None
