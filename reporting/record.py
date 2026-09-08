"""
The structured TEN Capital record (spec section 16).

Emitted alongside the PDF as a machine-readable JSON document shaped for a CRM,
API, database or analytics sink.

ARCHITECTURAL DECISION -- an explicit, stable export schema
-----------------------------------------------------------
The record is built by an explicit mapping rather than by dumping the internal
Pydantic models. Internal models change as the analysis evolves; a record that
downstream systems ingest must not. The mapping is the contract, and
`SCHEMA_VERSION` is bumped when it changes.

The record carries the full evidence trail (`sources`, `evidence_type`,
`confidence`) that the one-pager can only hint at, so a CRM row remains
auditable back to a slide number long after the PDF has been filed.

Transmission is separate and stricter: `transmit` refuses without explicit
user consent AND a configured endpoint. Writing to disk is not transmission.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from config import Settings, settings as default_settings
from models.schemas import (
    PROFILE_FIELDS,
    CompetitiveAnalysis,
    Competitor,
    Fact,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"


def _fact(fact: Fact) -> Dict[str, Any]:
    """Serialise a Fact with its provenance intact."""
    return {
        "value": fact.value,
        "evidence_type": fact.evidence_type.value,
        "confidence": round(fact.confidence, 3),
        "sources": [
            {
                "source_type": s.source_type.value,
                "locator": s.locator,
                "url": s.url,
                "excerpt": s.excerpt,
            }
            for s in fact.sources
        ],
    }


def _competitor(c: Competitor) -> Dict[str, Any]:
    return {
        "name": c.name,
        "website": c.website,
        "category": c.category.value,
        "headquarters": c.headquarters,
        "founded_year": c.founded_year,
        "target_customer": c.target_customer,
        "product": c.core_product,
        "positioning": c.positioning,
        "primary_use_case": c.primary_use_case,
        "business_model": c.business_model,
        "pricing": c.pricing,
        "revenue": c.revenue,
        "traction": c.traction,
        "customer_count": c.customer_count,
        "user_count": c.user_count,
        "funding": c.funding_raised,
        "latest_round": c.latest_round,
        "valuation": c.valuation,
        "investors": c.investors,
        "distribution": c.distribution,
        "geographic_reach": c.geographic_reach,
        "partnerships": c.partnerships,
        "strengths": c.strengths,
        "weaknesses": c.weaknesses,
        "defensibility": c.defensibility,
        "threat_level": c.threat_level.value,
        "threat_rationale": c.threat_rationale,
        "relevance": round(c.relevance, 3),
        "confidence": round(c.confidence, 3),
        "named_in_deck": c.named_in_deck,
        "sources": [
            {"source_type": s.source_type.value, "locator": s.locator, "url": s.url}
            for s in c.sources
        ],
    }


def build_record(analysis: CompetitiveAnalysis) -> Dict[str, Any]:
    """Assemble the complete machine-readable record."""
    p = analysis.profile
    s = analysis.score
    d = analysis.diagnostics

    return {
        "schema_version": SCHEMA_VERSION,
        "user": {
            "name": analysis.user.user_name,
            "email": analysis.user.user_email,
        },
        "company": {
            "name": p.company_name.value,
            "website": p.website.value,
            "sector": p.sector.value,
            "subsector": p.subsector.value,
            "stage": p.funding_stage.value or p.current_round.value,
        },
        "analysis": {
            "differentiation_score": s.total,
            "confidence_score": round(s.confidence, 3),
            "band": s.band,
            "competitive_position": analysis.assessment,
            "recommended_positioning": analysis.positioning.recommended,
            "competitor_count": len(analysis.competitors),
            "score_explanation": s.explanation,
            "category_scores": [
                {
                    "name": c.name,
                    "weight": c.weight,
                    "achievement": round(c.raw, 3),
                    "weighted_points": round(c.weighted, 2),
                    "rationale": c.rationale,
                    "evidence_backed": c.evidence_backed,
                }
                for c in s.categories
            ],
            "unevidenced_claims_discounted": s.unevidenced_claims_discounted,
        },
        "company_profile": {name: _fact(p.get(name)) for name in PROFILE_FIELDS},
        "profile_gaps": p.missing_fields(),
        "extraction_notes": p.extraction_notes,
        "competitors": [_competitor(c) for c in analysis.competitors],
        "competitive_dimensions": [
            {
                "dimension": dim.dimension,
                "advantage": dim.advantage.value,
                "company_position": dim.company_position,
                "competitor_position": dim.competitor_position,
                "evidence": dim.evidence,
                "evidence_type": dim.evidence_type.value,
                "confidence": round(dim.confidence, 3),
                "sustainability": dim.sustainability,
            }
            for dim in analysis.dimensions
        ],
        "market_map": (
            {
                "axis_x": analysis.market_map.axis_x.model_dump(),
                "axis_y": analysis.market_map.axis_y.model_dump(),
                "rationale": analysis.market_map.rationale,
                "entries": [
                    {
                        "name": e.name,
                        "x": round(e.x, 3),
                        "y": round(e.y, 3),
                        "is_company": e.is_company,
                        "category": e.category.value,
                    }
                    for e in analysis.market_map.entries
                ],
            }
            if analysis.market_map
            else None
        ),
        "whitespace_opportunities": [
            {
                "opportunity": w.opportunity,
                "evidence": w.evidence,
                "rationale": w.rationale,
                "difficulty": w.difficulty,
                "investor_relevance": w.investor_relevance,
                "evidence_type": w.evidence_type.value,
            }
            for w in analysis.whitespace
        ],
        # `risks` and `diligence_questions` are separate top-level keys per the
        # spec's schema, but are derived from the same concern objects so they
        # can never drift apart.
        "risks": [
            {"concern": c.concern, "current_risk": c.current_risk, "severity": c.severity}
            for c in analysis.concerns
        ],
        "diligence_questions": [
            {
                "question": c.diligence_question,
                "evidence_needed": c.evidence_needed,
                "relates_to": c.concern,
                "severity": c.severity,
            }
            for c in analysis.concerns
        ],
        "positioning": {
            "current_implied": analysis.positioning.current_implied,
            "recommended": analysis.positioning.recommended,
            "word_count": analysis.positioning.word_count,
            "proof_points_required": analysis.positioning.proof_points_required,
            "claims_to_avoid": analysis.positioning.claims_to_avoid,
            "competitive_narrative": analysis.positioning.competitive_narrative,
        },
        "consent": {
            "status": analysis.consent.status,
            "public_research_permitted": analysis.consent.public_research_permitted,
            "external_transmission_permitted": analysis.consent.external_transmission_permitted,
            "timestamp": analysis.consent.timestamp,
        },
        "run": {
            "llm_available": d.llm_available,
            "llm_calls": d.llm_calls,
            "research_performed": d.research_performed,
            "degraded": d.degraded,
            "errors": d.errors,
            "warnings": d.warnings,
            "notes": d.info,
            "pdf_reduction_steps": d.pdf_reduction_steps,
        },
        "analysis_timestamp": analysis.analysis_timestamp,
    }


def write_record(analysis: CompetitiveAnalysis, output_path: str | Path) -> Path:
    """Write the JSON record to disk. Local write; not transmission."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    record = build_record(analysis)
    output_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Record written to %s", output_path)
    return output_path


def transmit(
    analysis: CompetitiveAnalysis,
    settings: Optional[Settings] = None,
    timeout: float = 30.0,
) -> tuple[bool, str]:
    """POST the record to the configured TEN Capital endpoint.

    Refuses unless the user explicitly authorised external transmission *and*
    an endpoint is configured. Returns `(sent, message)` and never raises.
    """
    settings = settings or default_settings

    if not analysis.consent.external_transmission_permitted:
        return False, "Not transmitted: the user did not authorise external transmission."
    if not settings.crm_endpoint:
        return False, "Not transmitted: no TEN_CRM_ENDPOINT is configured."

    try:
        try:
            import httpx2 as httpx
        except ImportError:  # pragma: no cover
            import httpx  # type: ignore

        headers = {"Content-Type": "application/json"}
        if settings.crm_api_key:
            headers["Authorization"] = f"Bearer {settings.crm_api_key}"

        response = httpx.post(
            settings.crm_endpoint,
            json=build_record(analysis),
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return True, f"Record transmitted to {settings.crm_endpoint} (HTTP {response.status_code})."
    except Exception as exc:
        return False, f"Transmission failed: {exc.__class__.__name__}: {exc}"
