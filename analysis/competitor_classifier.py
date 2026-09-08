"""
Competitor set hygiene: deduplication, false-competitor filtering, category
coverage and ranking.

Deliberately deterministic and model-free. Discovery is a judgement call worth
spending a model on; deciding that "Availity" and "Availity Essentials" are the
same row is not, and a rule you can read is easier to trust than one you cannot.

The filtering philosophy follows spec section 23: a long competitor list is
worse than a short relevant one, because the report has one page and an investor
has two minutes.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Tuple

from config import Settings, settings as default_settings
from models.schemas import (
    CATEGORY_LABEL,
    Competitor,
    CompetitorCategory,
    RunDiagnostics,
)

logger = logging.getLogger(__name__)

#: Corporate suffixes and decorations stripped before comparing names.
_NOISE_RE = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|co|plc|gmbh|sa|nv|ag|holdings|group|"
    r"technologies|technology|tech|systems|solutions|software|health|healthcare|labs|ai)\b\.?",
    re.IGNORECASE,
)
_PAREN_RE = re.compile(r"\([^)]*\)")

#: Below both of these a record is not worth a row on a one-page report.
MIN_RELEVANCE = 0.25
MIN_CONFIDENCE = 0.25


def canonical(name: str) -> str:
    """Comparison key for a company name."""
    name = _PAREN_RE.sub(" ", name or "")
    name = re.sub(r"[^\w\s]", " ", name)
    name = _NOISE_RE.sub(" ", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def _same_company(a: Competitor, b: Competitor) -> bool:
    ca, cb = canonical(a.name), canonical(b.name)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    # One name containing the other ("Optum" vs "Optum / Change Healthcare"),
    # but only when the shorter is substantial enough to be distinctive.
    shorter, longer = sorted((ca, cb), key=len)
    return len(shorter) >= 5 and shorter in longer


def _merge(primary: Competitor, other: Competitor) -> Competitor:
    """Fold a duplicate into the record we are keeping, preferring real data."""
    from models.schemas import NOT_DISCLOSED, UNKNOWN, is_nullish

    merged = primary.model_copy(deep=True)
    for field in (
        "website", "headquarters", "founded_year", "target_customer", "core_product",
        "positioning", "primary_use_case", "business_model", "pricing", "revenue",
        "traction", "customer_count", "user_count", "funding_raised", "latest_round",
        "valuation", "distribution", "geographic_reach", "partnerships", "defensibility",
    ):
        current = getattr(merged, field, None)
        candidate = getattr(other, field, None)
        if (current in (None, UNKNOWN, NOT_DISCLOSED) or is_nullish(current)) and candidate:
            if candidate not in (UNKNOWN, NOT_DISCLOSED) and not is_nullish(candidate):
                setattr(merged, field, candidate)

    merged.strengths = _dedupe_list(merged.strengths + other.strengths)[:3]
    merged.weaknesses = _dedupe_list(merged.weaknesses + other.weaknesses)[:3]
    merged.investors = _dedupe_list(merged.investors + other.investors)
    merged.sources = merged.sources + [s for s in other.sources if s not in merged.sources]
    merged.named_in_deck = merged.named_in_deck or other.named_in_deck
    merged.relevance = max(merged.relevance, other.relevance)
    merged.confidence = max(merged.confidence, other.confidence)
    # Keep the more severe threat assessment.
    from models.schemas import THREAT_RANK

    if THREAT_RANK[other.threat_level] > THREAT_RANK[merged.threat_level]:
        merged.threat_level = other.threat_level
        merged.threat_rationale = other.threat_rationale
    return merged


def _dedupe_list(items: List[str]) -> List[str]:
    seen, out = set(), []
    for item in items:
        key = re.sub(r"\s+", " ", (item or "")).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def deduplicate(competitors: List[Competitor]) -> Tuple[List[Competitor], int]:
    """Collapse records that refer to the same company."""
    kept: List[Competitor] = []
    removed = 0
    for candidate in competitors:
        for i, existing in enumerate(kept):
            if _same_company(existing, candidate):
                kept[i] = _merge(existing, candidate)
                removed += 1
                break
        else:
            kept.append(candidate)
    return kept, removed


def filter_weak(competitors: List[Competitor]) -> Tuple[List[Competitor], List[str]]:
    """Drop records too speculative or too tangential to inform a decision.

    A competitor named in the deck is always kept: even a weak one is worth a
    row, because the investor will have seen the deck's competitor slide.
    """
    kept, dropped = [], []
    for c in competitors:
        if c.named_in_deck:
            kept.append(c)
            continue
        if c.relevance < MIN_RELEVANCE and c.confidence < MIN_CONFIDENCE:
            dropped.append(f"{c.name} (relevance {c.relevance:.2f}, confidence {c.confidence:.2f})")
            continue
        if c.confidence < 0.2:
            dropped.append(f"{c.name} (confidence {c.confidence:.2f} — existence not established)")
            continue
        kept.append(c)
    return kept, dropped


def category_counts(competitors: List[Competitor]) -> Dict[CompetitorCategory, int]:
    counts = {cat: 0 for cat in CompetitorCategory}
    for c in competitors:
        counts[c.category] += 1
    return counts


def select(
    competitors: List[Competitor],
    diagnostics: RunDiagnostics,
    settings: Settings | None = None,
) -> List[Competitor]:
    """Clean, cap and rank the competitive set.

    The cap keeps at least one of each represented category before filling the
    remaining slots by priority, so trimming to fit never silently deletes the
    only substitute or the only incumbent -- the two categories investors most
    often find missing from a founder's own analysis.
    """
    settings = settings or default_settings

    if not competitors:
        diagnostics.warn("No competitors were identified; the competitive analysis is empty.")
        return []

    competitors, merged = deduplicate(competitors)
    if merged:
        diagnostics.note(f"Merged {merged} duplicate competitor record(s).")

    competitors, dropped = filter_weak(competitors)
    if dropped:
        diagnostics.note("Filtered as insufficiently relevant: " + "; ".join(dropped[:4]))

    competitors.sort(key=lambda c: c.priority, reverse=True)

    counts = category_counts(competitors)
    if counts[CompetitorCategory.SUBSTITUTE] == 0:
        diagnostics.warn(
            "No substitute was identified. For most early-stage companies the status quo — "
            "manual process, in-house build, or doing nothing — is the alternative that "
            "actually wins deals. Treat its absence as a gap in the analysis, not as good news."
        )
    if counts[CompetitorCategory.INCUMBENT] == 0:
        diagnostics.note(
            "No incumbent was identified. Confirm that no established vendor already owns "
            "this customer relationship or budget line."
        )

    if len(competitors) > settings.max_competitors:
        selected: List[Competitor] = []
        # One representative per category first, in priority order.
        for category in CompetitorCategory:
            for c in competitors:
                if c.category is category:
                    selected.append(c)
                    break
        for c in competitors:
            if len(selected) >= settings.max_competitors:
                break
            if c not in selected:
                selected.append(c)
        selected.sort(key=lambda c: c.priority, reverse=True)
        diagnostics.note(
            f"Retained the {len(selected)} most decision-relevant of "
            f"{len(competitors)} competitors identified."
        )
        competitors = selected

    if len(competitors) < settings.min_competitors:
        diagnostics.warn(
            f"Only {len(competitors)} competitor(s) met the relevance threshold "
            f"(target: {settings.min_competitors}). The landscape view is thin — treat the "
            "differentiation score as provisional."
        )

    logger.info(
        "Selected %d competitors: %s",
        len(competitors),
        ", ".join(f"{CATEGORY_LABEL[k]}={v}" for k, v in category_counts(competitors).items() if v),
    )
    return competitors
