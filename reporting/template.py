"""
The canonical document template.

ARCHITECTURAL DECISION -- the template is rendered, not drawn separately
------------------------------------------------------------------------
`build_template_analysis()` returns a `CompetitiveAnalysis` populated entirely
with placeholder tokens, which is then rendered through the *same*
`generate_pdf` pipeline as a real report. There is no second layout
implementation.

That matters: a template maintained separately from the renderer drifts the
moment a column is added or a section is reordered, and a stale template is
worse than none. Here, any change to the report changes the template
automatically, and a test asserts the two stay structurally identical.

`STRUCTURE` below is the human-readable contract -- every section, every field,
its source, and the constraint that governs it. It is emitted as Markdown and
JSON so the template is usable by people and by downstream systems, not only as
a picture of a page.

Nothing in this module contains company-specific content.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import Settings, settings as default_settings
from models.schemas import (
    PROFILE_FIELDS,
    Advantage,
    CategoryScore,
    CompanyProfile,
    CompetitiveAnalysis,
    Competitor,
    CompetitorCategory,
    Consent,
    DifferentiationScore,
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

# --------------------------------------------------------------------------
# Placeholder vocabulary
# --------------------------------------------------------------------------

#: Every placeholder is bracketed so a template can never be mistaken for a
#: report, and so a half-filled document is greppable for leftovers.
PLACEHOLDER_PREFIX = "["


def ph(label: str) -> str:
    return f"[{label}]"


# --------------------------------------------------------------------------
# The structural contract
# --------------------------------------------------------------------------

#: Each entry: (field label, where the value comes from, the governing rule).
StructureField = Dict[str, str]


def _f(label: str, source: str, rule: str) -> StructureField:
    return {"field": label, "source": source, "rule": rule}


STRUCTURE: Dict[str, Any] = {
    "document": {
        "title": "Competitive Intelligence",
        "page_size": "US Letter (612 x 792 pt), portrait",
        "page_count": "Exactly 1 — enforced by the layout reduction ladder",
        "read_time": "Approximately 2 minutes",
        "audience": "Investment committee / partner meeting",
    },
    "sections": [
        {
            "number": None,
            "name": "Header",
            "purpose": "Identify the subject and the evidence base at a glance.",
            "fields": [
                _f("Report title", "Fixed", "Always 'COMPETITIVE INTELLIGENCE'"),
                _f("Company name", "profile.company_name", "Font auto-shrinks 17pt→10.5pt to fit one line"),
                _f("Sector", "profile.sector", "Omitted from the meta line if not established"),
                _f("Subsector", "profile.subsector", "Omitted if not established"),
                _f("Stage", "profile.funding_stage or current_round", "Omitted if not established"),
                _f("Date", "analysis_timestamp", "Format: DD Mon YYYY"),
                _f("Competitor count", "len(competitors)", "Right-aligned provenance badge"),
                _f("Evidence base", "diagnostics", "One of: Deck + public sources / Deck + market knowledge / Deck only — reduced confidence"),
                _f("Degradation flag", "diagnostics.degraded", "'DEGRADED RUN — see notes' shown only when the run degraded"),
            ],
        },
        {
            "number": 1,
            "name": "Competitive position",
            "purpose": "The headline judgement and the recommended positioning.",
            "fields": [
                _f("Differentiation score", "score.total", "Integer 0–100, rendered at 27pt, coloured by band"),
                _f("Score band", "score.band", "One of: Highly differentiated (80–100) / Meaningfully differentiated (65–79) / Moderate differentiation (50–64) / Weak differentiation (35–49) / Commodity or highly vulnerable (0–34)"),
                _f("Confidence score", "score.confidence", "0.00–1.00; below 0.50 the score is provisional"),
                _f("Score scale bar", "score.total", "0–100 strip with band boundaries at 35 / 50 / 65 / 80"),
                _f("Category breakdown", "score.categories", "Top 4 categories, bar widths proportional to weight"),
                _f("Short assessment", "assessment", "≤330 characters at full layout; states the real position including what is unproven"),
                _f("Recommended positioning", "positioning.recommended", "20–35 words; form: For [customer], [Company] is the [category] that [benefit], unlike [alternative], because [differentiation]"),
            ],
        },
        {
            "number": 2,
            "name": "Competitive matrix",
            "purpose": "Compare the subject against the most decision-relevant competitors.",
            "rows": "Subject company first (highlighted), then 4–7 competitors ranked by threat",
            "columns": [
                _f("Company", "competitor.name / profile.company_name", "Subject row shown in bold with a tinted background"),
                _f("Customer", "competitor.target_customer", "'Unknown' renders as an em dash"),
                _f("Product / approach", "competitor.core_product", "Max 2 wrapped lines per cell"),
                _f("Pricing", "competitor.pricing", "'Not publicly disclosed' renders as 'n/d'; never estimated"),
                _f("Traction", "competitor.traction / customer_count / revenue / funding", "First non-sentinel value; never estimated"),
                _f("Distribution", "competitor.distribution", "Incumbent distribution is treated as a competitive advantage"),
                _f("Primary advantage", "competitor.strengths[0] / defensibility", "Subject row shows its strongest EVIDENCED advantage, not its loudest claim"),
                _f("Threat", "competitor.threat_level", "high / medium / low, colour-coded; subject row shows an em dash"),
            ],
        },
        {
            "number": 3,
            "name": "Market map & whitespace",
            "purpose": "Show strategic position and the openings that follow from it.",
            "fields": [
                _f("X axis", "market_map.axis_x", "Chosen for this market; label plus low/high end labels"),
                _f("Y axis", "market_map.axis_y", "Must be independent of the X axis"),
                _f("Plotted entries", "market_map.entries", "Every competitor plus the subject; subject drawn larger in accent colour"),
                _f("Axis rationale", "market_map.rationale", "One sentence, right-aligned in the section header"),
                _f("Opportunity", "whitespace[].opportunity", "Top 3 shown; ≤14 words each"),
                _f("Supporting detail", "whitespace[].investor_relevance + evidence", "1–2 lines; must point at a gap in the competitive set"),
                _f("Difficulty", "whitespace[].difficulty", "low / medium / high, shown as three pips in the right gutter"),
            ],
        },
        {
            "number": 4,
            "name": "Investor watchlist",
            "purpose": "The competitive questions this deal will face, and what settles them.",
            "entries": "3–5, ranked by severity",
            "fields": [
                _f("Concern", "concerns[].concern", "≤12 words, phrased as an investor would ask it"),
                _f("Severity", "concerns[].severity", "high / medium / low; a coloured bar and right-aligned label"),
                _f("RISK", "concerns[].current_risk", "What the evidence establishes TODAY. A statement, never a question"),
                _f("ASK", "concerns[].diligence_question", "One specific question answerable by a founder in a meeting"),
                _f("NEED", "concerns[].evidence_needed", "A named artefact — contract, cohort curve, reference call. Never 'more data'"),
            ],
        },
        {
            "number": None,
            "name": "Claims to avoid",
            "purpose": "The most directly actionable line: what the company should stop saying.",
            "fields": [
                _f("Claims to avoid", "positioning.claims_to_avoid", "Up to 2 shown, each with the reason it fails; dropped first if space is short"),
            ],
        },
        {
            "number": None,
            "name": "Footer",
            "purpose": "Attribution, source key and confidentiality.",
            "fields": [
                _f("Attribution", "Fixed", "'TEN Capital Decision Intelligence'"),
                _f("Source legend", "Fixed", "DECK pitch deck · DOC uploaded · WEB company site · PUB public source · INF analyst inference"),
                _f("Confidentiality", "Fixed", "'Confidential – for recipients only.'"),
            ],
        },
    ],
    "governing_rules": [
        "Every factual assertion carries provenance: source type, locator, evidence type and confidence.",
        "Company claims are never rendered as facts. An uncited superlative earns no score.",
        "Unknown financial values render as 'Not publicly disclosed'; unknown qualitative values as 'Unknown'. Neither is ever estimated.",
        "Competition is for the same customer, workflow and budget — not merely the same sector.",
        "Substitutes (manual process, in-house build, outsourcing, doing nothing) are first-class competitors.",
        "The document is exactly one page. Content is reduced in priority order rather than allowed to overflow.",
        "A degraded run is visibly marked as such and can never be mistaken for a complete one.",
    ],
    "reduction_order": [
        "1. Shorten the narrative (assessment and supporting text)",
        "2. Reduce competitors shown, never below 4",
        "3. Drop the score breakdown, then the market map, then the claims-to-avoid line",
        "4. Reduce the watchlist last — it is the highest-value section",
        "5. Never overflow onto a second page",
    ],
}


# --------------------------------------------------------------------------
# The blank analysis
# --------------------------------------------------------------------------


def _placeholder_fact(label: str) -> Fact:
    return Fact(
        value=ph(label),
        evidence_type=EvidenceType.UNVERIFIED,
        confidence=0.0,
        sources=[Evidence(source_type=SourceType.UNKNOWN, locator=ph("SOURCE"))],
    )


def _template_profile() -> CompanyProfile:
    """Every profile field filled with its own name as a placeholder."""
    profile = CompanyProfile()
    for name in PROFILE_FIELDS:
        setattr(profile, name, _placeholder_fact(name.replace("_", " ").upper()))
    return profile


def _template_competitors(count: int) -> List[Competitor]:
    """One competitor row per category, so every category is represented."""
    categories = [
        CompetitorCategory.SUBSTITUTE,
        CompetitorCategory.INCUMBENT,
        CompetitorCategory.DIRECT,
        CompetitorCategory.INDIRECT,
        CompetitorCategory.EMERGING,
        CompetitorCategory.DIRECT,
        CompetitorCategory.INCUMBENT,
    ]
    threats = [ThreatLevel.HIGH, ThreatLevel.HIGH, ThreatLevel.HIGH,
               ThreatLevel.MEDIUM, ThreatLevel.MEDIUM, ThreatLevel.LOW, ThreatLevel.LOW]

    out: List[Competitor] = []
    for i in range(count):
        category = categories[i % len(categories)]
        out.append(
            Competitor(
                name=ph(f"COMPETITOR {i + 1} — {category.value.upper()}"),
                category=category,
                target_customer=ph("TARGET CUSTOMER"),
                core_product=ph("CORE PRODUCT / APPROACH"),
                positioning=ph("POSITIONING"),
                primary_use_case=ph("WHY IT COMPETES FOR THE SAME BUDGET"),
                pricing=ph("PRICING OR 'Not publicly disclosed'"),
                traction=ph("TRACTION OR 'Not publicly disclosed'"),
                distribution=ph("DISTRIBUTION"),
                defensibility=ph("DEFENSIBILITY"),
                strengths=[ph("KEY STRENGTH")],
                weaknesses=[ph("KEY WEAKNESS")],
                threat_level=threats[i % len(threats)],
                threat_rationale=ph("THREAT RATIONALE"),
                relevance=0.5,
                confidence=0.0,
            )
        )
    return out


def _template_score() -> DifferentiationScore:
    from config import SCORE_WEIGHTS

    return DifferentiationScore(
        total=0,
        confidence=0.0,
        band=ph("SCORE BAND"),
        explanation=ph("SCORE EXPLANATION"),
        categories=[
            CategoryScore(
                name=name,
                weight=weight,
                raw=0.5,                 # neutral bars: the strip's shape, not a result
                weighted=0.5 * weight,
                rationale=ph("CATEGORY RATIONALE"),
                evidence_backed=False,
            )
            for name, weight in SCORE_WEIGHTS
        ],
        unevidenced_claims_discounted=[ph("UNEVIDENCED CLAIM EXCLUDED FROM SCORING")],
    )


def _template_dimensions() -> List[DimensionAssessment]:
    from config import ALL_DIMENSIONS

    return [
        DimensionAssessment(
            dimension=name,
            advantage=Advantage.UNKNOWN,
            company_position=ph("COMPANY POSITION"),
            competitor_position=ph("COMPETITOR POSITION"),
            evidence=ph("EVIDENCE"),
            evidence_type=EvidenceType.UNVERIFIED,
            confidence=0.0,
            sustainability="unknown",
        )
        for name in ALL_DIMENSIONS
    ]


def _template_market_map(competitors: List[Competitor]) -> MarketMap:
    # Spread the entries so the plot shows how a populated map reads.
    spread = [(0.12, 0.20), (0.30, 0.72), (0.55, 0.45), (0.72, 0.82),
              (0.86, 0.30), (0.45, 0.15), (0.65, 0.62)]
    entries = [
        MarketMapEntry(
            name=ph(f"COMPETITOR {i + 1}"),
            x=spread[i % len(spread)][0],
            y=spread[i % len(spread)][1],
            category=c.category,
        )
        for i, c in enumerate(competitors)
    ]
    entries.append(MarketMapEntry(name=ph("COMPANY"), x=0.50, y=0.50, is_company=True))
    return MarketMap(
        axis_x=MarketAxis(label=ph("X AXIS"), low_label=ph("LOW"), high_label=ph("HIGH")),
        axis_y=MarketAxis(label=ph("Y AXIS"), low_label=ph("LOW"), high_label=ph("HIGH")),
        entries=entries,
        rationale=ph("WHY THESE AXES SEPARATE THIS MARKET"),
    )


def build_template_analysis(settings: Optional[Settings] = None) -> CompetitiveAnalysis:
    """A fully-populated `CompetitiveAnalysis` containing only placeholders."""
    settings = settings or default_settings
    competitors = _template_competitors(settings.matrix_rows + 1)

    diagnostics = RunDiagnostics(llm_available=True, research_performed=True)

    return CompetitiveAnalysis(
        profile=_template_profile(),
        competitors=competitors,
        dimensions=_template_dimensions(),
        score=_template_score(),
        market_map=_template_market_map(competitors),
        whitespace=[
            WhitespaceOpportunity(
                opportunity=ph(f"WHITESPACE OPPORTUNITY {i + 1} — ≤14 WORDS"),
                evidence=ph("THE GAP IN THE COMPETITIVE SET THAT IMPLIES IT"),
                rationale=ph("WHY THIS COMPANY COULD TAKE IT"),
                difficulty=("medium", "high", "low")[i % 3],
                investor_relevance=ph("WHAT IT CHANGES ABOUT THE RETURN CASE"),
            )
            for i in range(3)
        ],
        concerns=[
            InvestorConcern(
                concern=ph(f"INVESTOR CONCERN {i + 1} — ≤12 WORDS"),
                current_risk=ph("CURRENT RISK — WHAT THE EVIDENCE ESTABLISHES TODAY (A STATEMENT)"),
                diligence_question=ph("DILIGENCE QUESTION — ONE SPECIFIC, ANSWERABLE QUESTION"),
                evidence_needed=ph("EVIDENCE NEEDED — A NAMED ARTEFACT THAT WOULD SETTLE IT"),
                severity=("high", "high", "medium", "medium", "low")[i % 5],
            )
            for i in range(5)
        ],
        positioning=Positioning(
            current_implied=ph("CURRENT IMPLIED POSITIONING"),
            recommended=(
                "For [TARGET CUSTOMER], [COMPANY] is the [CATEGORY] that [PRIMARY BENEFIT], "
                "unlike [ALTERNATIVE], because [DEFENSIBLE DIFFERENTIATION]. "
                "Twenty to thirty-five words."
            ),
            proof_points_required=[ph("PROOF POINT REQUIRED")],
            claims_to_avoid=[
                ph("CLAIM TO AVOID — AND WHY IT FAILS"),
                ph("CLAIM TO AVOID — AND WHY IT FAILS"),
            ],
            competitive_narrative=ph("COMPETITIVE NARRATIVE — ONE SENTENCE"),
            word_count=0,
        ),
        assessment=(
            "[SHORT ASSESSMENT — two to three sentences an investor could read alone. "
            "States the real competitive position, including what is unproven. "
            "Approximately 330 characters at the full layout.]"
        ),
        consent=Consent(),
        diagnostics=diagnostics,
    )


# --------------------------------------------------------------------------
# Emitters
# --------------------------------------------------------------------------


def render_template_pdf(
    output_path: str | Path, settings: Optional[Settings] = None
) -> Path:
    """Render the blank one-page template through the real report pipeline."""
    from reporting.pdf_generator import generate_pdf

    settings = settings or default_settings
    return generate_pdf(build_template_analysis(settings), output_path, settings, template=True)


def template_record(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """The JSON record skeleton, with placeholders in place of values."""
    from reporting.record import build_record

    record = build_record(build_template_analysis(settings))
    # Strip run-specific values that carry no structural meaning.
    record["analysis_timestamp"] = ph("ISO 8601 TIMESTAMP")
    record["consent"]["timestamp"] = None
    record["run"] = {
        key: (ph("BOOLEAN") if isinstance(value, bool) else
              ph("INTEGER") if isinstance(value, int) else [])
        for key, value in record["run"].items()
    }
    record["analysis"]["differentiation_score"] = ph("INTEGER 0-100")
    record["analysis"]["confidence_score"] = ph("FLOAT 0.0-1.0")
    record["analysis"]["competitor_count"] = ph("INTEGER")
    return record


def write_template_record(
    output_path: str | Path, settings: Optional[Settings] = None
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(template_record(settings), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return output_path


def structure_markdown() -> str:
    """Render `STRUCTURE` as a readable specification."""
    doc = STRUCTURE["document"]
    lines: List[str] = [
        "# Competitive Intelligence — Document Structure Template",
        "",
        "The canonical structure of every report this application produces.",
        "It contains no company-specific content: only sections, fields, the source",
        "each field is drawn from, and the rule that governs it.",
        "",
        "## Document",
        "",
        "| Property | Value |",
        "| --- | --- |",
    ]
    for key, value in doc.items():
        lines.append(f"| {key.replace('_', ' ').capitalize()} | {value} |")

    for section in STRUCTURE["sections"]:
        number = f"{section['number']} · " if section["number"] else ""
        lines += ["", f"## {number}{section['name']}", "", f"*{section['purpose']}*", ""]
        for key in ("rows", "entries"):
            if key in section:
                lines += [f"**{key.capitalize()}:** {section[key]}", ""]
        columns = section.get("columns") or section.get("fields") or []
        label = "Column" if "columns" in section else "Field"
        lines += [f"| {label} | Source | Rule |", "| --- | --- | --- |"]
        for field in columns:
            lines.append(f"| {field['field']} | `{field['source']}` | {field['rule']} |")

    lines += ["", "## Governing rules", ""]
    lines += [f"{i + 1}. {rule}" for i, rule in enumerate(STRUCTURE["governing_rules"])]
    lines += ["", "## One-page reduction order", ""]
    lines += [f"- {step}" for step in STRUCTURE["reduction_order"]]
    lines.append("")
    return "\n".join(lines)


def write_structure_markdown(output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(structure_markdown(), encoding="utf-8")
    return output_path


def write_all(output_dir: str | Path, settings: Optional[Settings] = None) -> Dict[str, Path]:
    """Emit the complete template set: PDF skeleton, JSON skeleton, spec."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        "pdf": render_template_pdf(output_dir / "competitive_intelligence_template.pdf", settings),
        "json": write_template_record(output_dir / "competitive_intelligence_template.json", settings),
        "markdown": write_structure_markdown(output_dir / "DOCUMENT_STRUCTURE.md"),
    }
