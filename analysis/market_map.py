"""
Market map (spec section 9).

The axes are chosen for *this* market rather than pulled from a fixed
framework: the model proposes the two dimensions that actually separate the
players, and every entry -- including the company -- is placed on them.

Placement is a bounded 0..1 coordinate so the chart layer never has to guess
scale, and the company is always present even if the model forgets it.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel, Field

from llm.client import LLMClient
from models.schemas import (
    CompanyProfile,
    Competitor,
    CompetitorCategory,
    MarketAxis,
    MarketMap,
    MarketMapEntry,
    RunDiagnostics,
)

logger = logging.getLogger(__name__)


class AxisInput(BaseModel):
    label: str = Field(description="Axis name, 2-4 words, e.g. 'Workflow depth'.")
    low_label: str = Field(description="What the low end means, 1-3 words.")
    high_label: str = Field(description="What the high end means, 1-3 words.")


class PlacementInput(BaseModel):
    name: str = Field(description="Company name, matching the supplied list, or the subject company.")
    x: float = Field(description="0.0-1.0 position on the x axis.")
    y: float = Field(description="0.0-1.0 position on the y axis.")
    is_company: bool = Field(description="True only for the subject company.")


class MarketMapResult(BaseModel):
    axis_x: AxisInput
    axis_y: AxisInput
    placements: List[PlacementInput]
    rationale: str = Field(description="One sentence on why these axes separate this market.")


_MAP_RULES = """\
Choose the two axes that genuinely separate the players in THIS market and that
an investor would find decision-useful. Do not force a generic 2x2.

Candidate axis ideas (pick only if they fit, or invent better ones):
enterprise vs SMB; horizontal vs vertical; legacy vs AI-native; premium vs
low-cost; full platform vs point solution; hardware vs software; direct sales vs
channel; provider-side vs payer-side; workflow depth vs breadth of coverage;
build vs buy.

RULES
- The two axes must be independent. If the second axis is just a restatement of
  the first, choose a different one.
- Place every supplied competitor, and place the subject company.
- Use the full 0.0-1.0 range. If everything clusters in one corner, the axes
  are wrong -- choose axes that actually spread the market.
- Substitutes (in-house staffing, outsourcing, doing nothing) belong on the map
  too; they occupy real positions, usually at an extreme.
"""


def build(
    profile: CompanyProfile,
    competitors: List[Competitor],
    client: LLMClient,
    diagnostics: RunDiagnostics,
) -> Optional[MarketMap]:
    """Produce a market map, or None when there is nothing meaningful to plot."""
    if len(competitors) < 2:
        diagnostics.note("Market map omitted: fewer than two competitors to position against.")
        return None

    if not client.available:
        return _fallback_map(profile, competitors, diagnostics)

    listing = "\n".join(
        f"- {c.name} [{c.category.value}]: {c.core_product[:70]} | "
        f"customer: {c.target_customer[:45]} | distribution: {c.distribution[:45]}"
        for c in competitors
    )
    subject = (
        f"{profile.display_name()}: {profile.core_product.display('product not established')[:110]} | "
        f"customer: {profile.target_customer.display('not established')[:60]} | "
        f"model: {profile.business_model.display('not established')[:60]}"
    )

    prompt = (
        "Build a two-axis market map positioning the subject company against its competitors.\n\n"
        f"{_MAP_RULES}\n"
        f"===== SUBJECT COMPANY =====\n{subject}\n\n"
        f"===== COMPETITORS =====\n{listing}\n===== END ====="
    )

    result, error = client.structured(
        prompt, MarketMapResult, label="market-map", max_tokens=8000
    )
    if error or result is None:
        diagnostics.warn(f"Market map generation failed: {error}")
        return _fallback_map(profile, competitors, diagnostics)

    diagnostics.llm_calls += 1

    by_name = {c.name.lower(): c for c in competitors}
    entries: List[MarketMapEntry] = []
    company_placed = False

    for p in result.placements:
        match = by_name.get(p.name.strip().lower())
        is_company = p.is_company or (
            match is None and p.name.strip().lower() == profile.display_name().lower()
        )
        if is_company:
            company_placed = True
        entries.append(
            MarketMapEntry(
                name=p.name.strip(),
                x=p.x,
                y=p.y,
                is_company=is_company,
                category=match.category if match else CompetitorCategory.DIRECT,
            )
        )

    if not company_placed:
        # The subject must always appear; the map is meaningless without it.
        entries.append(
            MarketMapEntry(name=profile.display_name(), x=0.5, y=0.5, is_company=True)
        )
        diagnostics.note("Subject company was missing from the market map and was placed centrally.")

    return MarketMap(
        axis_x=MarketAxis(**result.axis_x.model_dump()),
        axis_y=MarketAxis(**result.axis_y.model_dump()),
        entries=entries,
        rationale=result.rationale,
    )


def _fallback_map(
    profile: CompanyProfile, competitors: List[Competitor], diagnostics: RunDiagnostics
) -> MarketMap:
    """Deterministic map used when no model is available.

    Axes are derived from data we already hold: competitor category (a proxy for
    incumbency) and threat level. Crude, but honest and always available.
    """
    from models.schemas import THREAT_RANK

    incumbency = {
        CompetitorCategory.SUBSTITUTE: 0.10,
        CompetitorCategory.EMERGING: 0.25,
        CompetitorCategory.DIRECT: 0.50,
        CompetitorCategory.INDIRECT: 0.60,
        CompetitorCategory.INCUMBENT: 0.90,
    }
    entries = [
        MarketMapEntry(
            name=c.name,
            x=incumbency.get(c.category, 0.5),
            y=(THREAT_RANK[c.threat_level] - 1) / 2,
            category=c.category,
        )
        for c in competitors
    ]
    entries.append(MarketMapEntry(name=profile.display_name(), x=0.20, y=0.5, is_company=True))

    diagnostics.note(
        "Market map uses fallback axes (incumbency vs threat) because no model was available."
    )
    return MarketMap(
        axis_x=MarketAxis(label="Market position", low_label="Challenger", high_label="Incumbent"),
        axis_y=MarketAxis(label="Threat level", low_label="Low", high_label="High"),
        entries=entries,
        rationale="Fallback axes derived from competitor category and assessed threat.",
    )
