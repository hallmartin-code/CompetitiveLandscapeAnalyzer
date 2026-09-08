"""
Evidence construction, grading and merge rules.

This module owns the answer to two questions the rest of the system keeps
asking: "what kind of claim is this?" and "when two sources disagree, which
wins?". Centralising them keeps the epistemics consistent -- and testable --
rather than re-implemented per extractor.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

from models.schemas import (
    Evidence,
    EvidenceType,
    Fact,
    SourceDocument,
    SourceType,
    is_nullish,
)

#: Precedence when two sources offer a value for the same field. Higher wins.
#: The user is trusted most (they are the analyst operating the tool), then
#: things physically written down, then anything the system reasoned out.
SOURCE_PRECEDENCE: Dict[SourceType, int] = {
    SourceType.USER_PROVIDED: 60,
    SourceType.PITCH_DECK: 50,
    SourceType.UPLOADED_DOCUMENT: 45,
    SourceType.COMPANY_WEBSITE: 35,
    SourceType.PUBLIC_SOURCE: 40,
    SourceType.ANALYST_INFERENCE: 10,
    SourceType.UNKNOWN: 0,
}

_SOURCE_ALIASES = {
    "pitch_deck": SourceType.PITCH_DECK,
    "pitchdeck": SourceType.PITCH_DECK,
    "deck": SourceType.PITCH_DECK,
    "uploaded_document": SourceType.UPLOADED_DOCUMENT,
    "document": SourceType.UPLOADED_DOCUMENT,
    "company_website": SourceType.COMPANY_WEBSITE,
    "website": SourceType.COMPANY_WEBSITE,
    "public_source": SourceType.PUBLIC_SOURCE,
    "public": SourceType.PUBLIC_SOURCE,
    "user_provided": SourceType.USER_PROVIDED,
    "user": SourceType.USER_PROVIDED,
    "analyst_inference": SourceType.ANALYST_INFERENCE,
    "inference": SourceType.ANALYST_INFERENCE,
    "unknown": SourceType.UNKNOWN,
}

_EVIDENCE_ALIASES = {
    "stated_fact": EvidenceType.STATED_FACT,
    "fact": EvidenceType.STATED_FACT,
    "company_claim": EvidenceType.COMPANY_CLAIM,
    "claim": EvidenceType.COMPANY_CLAIM,
    "analyst_inference": EvidenceType.ANALYST_INFERENCE,
    "inference": EvidenceType.ANALYST_INFERENCE,
    "unverified": EvidenceType.UNVERIFIED,
    "missing": EvidenceType.MISSING,
}

#: Locator patterns we recognise inside model-supplied source strings.
_LOCATOR_RE = re.compile(r"\b(slide|page)\s*#?\s*(\d{1,3})\b", re.IGNORECASE)

#: Marketing intensifiers that signal an unevidenced superiority claim.
#: Used by `is_unevidenced_superlative` to stop the scorer awarding points for
#: adjectives -- spec section 7: "Do not award points for differentiation
#: claimed by the company without evidence."
_SUPERLATIVE_RE = re.compile(
    r"\b(\d+\s*x\s+(faster|better|cheaper|more)|only\s+(solution|company|platform)|"
    r"first\s+ever|best[- ]in[- ]class|world[- ]class|revolutionary|game[- ]?chang\w+|"
    r"unmatched|unparalleled|cutting[- ]edge|state[- ]of[- ]the[- ]art|"
    r"industry[- ]leading|superior|seamless(ly)?|10x|100x)\b",
    re.IGNORECASE,
)

#: A citation-looking token: a named source, a year-stamped study, a registry id.
_CITATION_RE = re.compile(
    r"(\(\s*[A-Z][A-Za-z&.\- ]{2,40}\s+(19|20)\d{2}\s*\)|"
    r"\b(19|20)\d{2}\s+(survey|study|report|trial|analysis)\b|"
    r"\bper\s+[A-Z][A-Za-z&.\- ]{2,40}\b|"
    r"\bNCT\d{8}\b|\bdoi:|\bhttps?://)",
    re.IGNORECASE,
)


def parse_source_type(raw: Optional[str]) -> SourceType:
    if not raw:
        return SourceType.UNKNOWN
    return _SOURCE_ALIASES.get(str(raw).strip().lower().replace(" ", "_"), SourceType.UNKNOWN)


def parse_evidence_type(raw: Optional[str]) -> EvidenceType:
    if not raw:
        return EvidenceType.UNVERIFIED
    return _EVIDENCE_ALIASES.get(
        str(raw).strip().lower().replace(" ", "_"), EvidenceType.UNVERIFIED
    )


def normalise_locator(raw: Optional[str]) -> str:
    """Turn a free-form source string into a canonical 'Slide 7' / 'Page 3'."""
    if not raw:
        return ""
    match = _LOCATOR_RE.search(raw)
    if match:
        return f"{match.group(1).capitalize()} {int(match.group(2))}"
    return str(raw).strip()[:60]


def build_evidence(
    locator: Optional[str],
    source_type: Optional[str] = None,
    excerpt: str = "",
    url: Optional[str] = None,
) -> Evidence:
    """Construct an `Evidence` from loosely-typed model output.

    When the model gives a locator but no source type, a slide/page reference
    implies the deck -- the only place slides exist.
    """
    st = parse_source_type(source_type)
    loc = normalise_locator(locator)
    if st is SourceType.UNKNOWN and _LOCATOR_RE.search(loc or ""):
        st = SourceType.PITCH_DECK
    if st is SourceType.UNKNOWN and url:
        st = SourceType.PUBLIC_SOURCE
    if st is SourceType.UNKNOWN and loc:
        # Models describe provenance in prose ("analyst inference", "company
        # website"); recover the type from the locator rather than losing it.
        low = loc.lower()
        for key, mapped in (
            ("inference", SourceType.ANALYST_INFERENCE),
            ("inferred", SourceType.ANALYST_INFERENCE),
            ("general knowledge", SourceType.ANALYST_INFERENCE),
            ("market knowledge", SourceType.ANALYST_INFERENCE),
            ("website", SourceType.COMPANY_WEBSITE),
            ("public", SourceType.PUBLIC_SOURCE),
            ("user", SourceType.USER_PROVIDED),
            ("deck", SourceType.PITCH_DECK),
            ("notes", SourceType.PITCH_DECK),
        ):
            if key in low:
                st = mapped
                if mapped is SourceType.ANALYST_INFERENCE:
                    loc = ""  # the badge already says INF; the prose adds nothing
                break
    return Evidence(source_type=st, locator=loc, excerpt=(excerpt or "")[:300], url=url)


def make_fact(
    value: Optional[str],
    locator: Optional[str] = None,
    source_type: Optional[str] = None,
    evidence_type: Optional[str] = None,
    confidence: float = 0.5,
    excerpt: str = "",
    url: Optional[str] = None,
) -> Fact:
    """Assemble a `Fact` from loosely-typed extractor output."""
    if is_nullish(value):
        return Fact.missing()
    ev = build_evidence(locator, source_type, excerpt, url)
    et = parse_evidence_type(evidence_type)
    if et is EvidenceType.UNVERIFIED and ev.source_type is SourceType.ANALYST_INFERENCE:
        et = EvidenceType.ANALYST_INFERENCE
    if ev.source_type is SourceType.UNKNOWN and et is EvidenceType.ANALYST_INFERENCE:
        # Keep the two provenance axes consistent: an inference is sourced from
        # this system, whatever the model wrote in the locator field.
        ev = Evidence(source_type=SourceType.ANALYST_INFERENCE, excerpt=ev.excerpt, url=ev.url)
    return Fact(
        value=str(value).strip(),
        evidence_type=et,
        confidence=confidence,
        sources=[ev] if (ev.locator or ev.url or ev.source_type is not SourceType.UNKNOWN) else [],
    )


def merge_fact(existing: Fact, candidate: Fact) -> Fact:
    """Resolve two candidate values for the same field.

    Precedence is by source authority first, confidence second. A known value
    always beats an unknown one -- but conflicting known values are surfaced,
    not hidden: the loser is appended to the winner's source list so the
    disagreement stays auditable.
    """
    if not candidate.known:
        return existing
    if not existing.known:
        return candidate

    def rank(f: Fact) -> tuple:
        top = max((SOURCE_PRECEDENCE.get(s.source_type, 0) for s in f.sources), default=0)
        return (top, f.confidence)

    winner, loser = (existing, candidate) if rank(existing) >= rank(candidate) else (candidate, existing)

    if winner.value and loser.value and winner.value.strip().lower() != loser.value.strip().lower():
        merged = winner.model_copy(deep=True)
        merged.sources = list(winner.sources) + [
            Evidence(
                source_type=(loser.sources[0].source_type if loser.sources else SourceType.UNKNOWN),
                locator=(loser.sources[0].locator if loser.sources else ""),
                excerpt=f"Conflicting value: {loser.value}",
            )
        ]
        return merged
    return winner


def is_unevidenced_superlative(text: Optional[str]) -> bool:
    """True when a claim is a marketing superlative with no citation attached.

    Used to prevent the differentiation score rewarding adjectives.
    """
    if not text:
        return False
    return bool(_SUPERLATIVE_RE.search(text)) and not bool(_CITATION_RE.search(text))


def has_citation(text: Optional[str]) -> bool:
    return bool(text) and bool(_CITATION_RE.search(text))


def grade_claim(text: Optional[str], source_type: SourceType) -> EvidenceType:
    """Classify a raw statement into an evidence type.

    A deck statement that cites an external source is treated as a stated fact;
    an uncited one is a company claim, however confidently phrased.
    """
    if is_nullish(text):
        return EvidenceType.MISSING
    if source_type in {SourceType.USER_PROVIDED}:
        return EvidenceType.STATED_FACT
    if source_type is SourceType.ANALYST_INFERENCE:
        return EvidenceType.ANALYST_INFERENCE
    if has_citation(text):
        return EvidenceType.STATED_FACT
    if source_type in {SourceType.PITCH_DECK, SourceType.COMPANY_WEBSITE}:
        return EvidenceType.COMPANY_CLAIM
    if source_type is SourceType.PUBLIC_SOURCE:
        return EvidenceType.STATED_FACT
    return EvidenceType.UNVERIFIED


def locate_in_documents(needle: str, documents: Iterable[SourceDocument]) -> List[Evidence]:
    """Find where a phrase actually appears, so a citation can be verified.

    Used to spot-check model-supplied locators: a claimed 'Slide 5' that cannot
    be found anywhere in the deck is downgraded rather than trusted.
    """
    hits: List[Evidence] = []
    probe = re.sub(r"\s+", " ", (needle or "")).strip().lower()
    if len(probe) < 8:
        return hits
    for doc in documents:
        for block in doc.blocks:
            haystack = re.sub(r"\s+", " ", block.text).lower()
            if probe in haystack:
                hits.append(
                    Evidence(
                        source_type=doc.source_type,
                        locator=block.locator,
                        excerpt=block.text[:200],
                    )
                )
                break
    return hits
