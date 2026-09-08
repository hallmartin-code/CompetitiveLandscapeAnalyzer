# Competitive Intelligence — Document Structure Template

The canonical structure of every report this application produces.
It contains no company-specific content: only sections, fields, the source
each field is drawn from, and the rule that governs it.

## Document

| Property | Value |
| --- | --- |
| Title | Competitive Intelligence |
| Page size | US Letter (612 x 792 pt), portrait |
| Page count | Exactly 1 — enforced by the layout reduction ladder |
| Read time | Approximately 2 minutes |
| Audience | Investment committee / partner meeting |

## Header

*Identify the subject and the evidence base at a glance.*

| Field | Source | Rule |
| --- | --- | --- |
| Report title | `Fixed` | Always 'COMPETITIVE INTELLIGENCE' |
| Company name | `profile.company_name` | Font auto-shrinks 17pt→10.5pt to fit one line |
| Sector | `profile.sector` | Omitted from the meta line if not established |
| Subsector | `profile.subsector` | Omitted if not established |
| Stage | `profile.funding_stage or current_round` | Omitted if not established |
| Date | `analysis_timestamp` | Format: DD Mon YYYY |
| Competitor count | `len(competitors)` | Right-aligned provenance badge |
| Evidence base | `diagnostics` | One of: Deck + public sources / Deck + market knowledge / Deck only — reduced confidence |
| Degradation flag | `diagnostics.degraded` | 'DEGRADED RUN — see notes' shown only when the run degraded |

## 1 · Competitive position

*The headline judgement and the recommended positioning.*

| Field | Source | Rule |
| --- | --- | --- |
| Differentiation score | `score.total` | Integer 0–100, rendered at 27pt, coloured by band |
| Score band | `score.band` | One of: Highly differentiated (80–100) / Meaningfully differentiated (65–79) / Moderate differentiation (50–64) / Weak differentiation (35–49) / Commodity or highly vulnerable (0–34) |
| Confidence score | `score.confidence` | 0.00–1.00; below 0.50 the score is provisional |
| Score scale bar | `score.total` | 0–100 strip with band boundaries at 35 / 50 / 65 / 80 |
| Category breakdown | `score.categories` | Top 4 categories, bar widths proportional to weight |
| Short assessment | `assessment` | ≤330 characters at full layout; states the real position including what is unproven |
| Recommended positioning | `positioning.recommended` | 20–35 words; form: For [customer], [Company] is the [category] that [benefit], unlike [alternative], because [differentiation] |

## 2 · Competitive matrix

*Compare the subject against the most decision-relevant competitors.*

**Rows:** Subject company first (highlighted), then 4–7 competitors ranked by threat

| Column | Source | Rule |
| --- | --- | --- |
| Company | `competitor.name / profile.company_name` | Subject row shown in bold with a tinted background |
| Customer | `competitor.target_customer` | 'Unknown' renders as an em dash |
| Product / approach | `competitor.core_product` | Max 2 wrapped lines per cell |
| Pricing | `competitor.pricing` | 'Not publicly disclosed' renders as 'n/d'; never estimated |
| Traction | `competitor.traction / customer_count / revenue / funding` | First non-sentinel value; never estimated |
| Distribution | `competitor.distribution` | Incumbent distribution is treated as a competitive advantage |
| Primary advantage | `competitor.strengths[0] / defensibility` | Subject row shows its strongest EVIDENCED advantage, not its loudest claim |
| Threat | `competitor.threat_level` | high / medium / low, colour-coded; subject row shows an em dash |

## 3 · Market map & whitespace

*Show strategic position and the openings that follow from it.*

| Field | Source | Rule |
| --- | --- | --- |
| X axis | `market_map.axis_x` | Chosen for this market; label plus low/high end labels |
| Y axis | `market_map.axis_y` | Must be independent of the X axis |
| Plotted entries | `market_map.entries` | Every competitor plus the subject; subject drawn larger in accent colour |
| Axis rationale | `market_map.rationale` | One sentence, right-aligned in the section header |
| Opportunity | `whitespace[].opportunity` | Top 3 shown; ≤14 words each |
| Supporting detail | `whitespace[].investor_relevance + evidence` | 1–2 lines; must point at a gap in the competitive set |
| Difficulty | `whitespace[].difficulty` | low / medium / high, shown as three pips in the right gutter |

## 4 · Investor watchlist

*The competitive questions this deal will face, and what settles them.*

**Entries:** 3–5, ranked by severity

| Field | Source | Rule |
| --- | --- | --- |
| Concern | `concerns[].concern` | ≤12 words, phrased as an investor would ask it |
| Severity | `concerns[].severity` | high / medium / low; a coloured bar and right-aligned label |
| RISK | `concerns[].current_risk` | What the evidence establishes TODAY. A statement, never a question |
| ASK | `concerns[].diligence_question` | One specific question answerable by a founder in a meeting |
| NEED | `concerns[].evidence_needed` | A named artefact — contract, cohort curve, reference call. Never 'more data' |

## Claims to avoid

*The most directly actionable line: what the company should stop saying.*

| Field | Source | Rule |
| --- | --- | --- |
| Claims to avoid | `positioning.claims_to_avoid` | Up to 2 shown, each with the reason it fails; dropped first if space is short |

## Footer

*Attribution, source key and confidentiality.*

| Field | Source | Rule |
| --- | --- | --- |
| Attribution | `Fixed` | 'TEN Capital Decision Intelligence' |
| Source legend | `Fixed` | DECK pitch deck · DOC uploaded · WEB company site · PUB public source · INF analyst inference |
| Confidentiality | `Fixed` | 'Confidential – for recipients only.' |

## Governing rules

1. Every factual assertion carries provenance: source type, locator, evidence type and confidence.
2. Company claims are never rendered as facts. An uncited superlative earns no score.
3. Unknown financial values render as 'Not publicly disclosed'; unknown qualitative values as 'Unknown'. Neither is ever estimated.
4. Competition is for the same customer, workflow and budget — not merely the same sector.
5. Substitutes (manual process, in-house build, outsourcing, doing nothing) are first-class competitors.
6. The document is exactly one page. Content is reduced in priority order rather than allowed to overflow.
7. A degraded run is visibly marked as such and can never be mistaken for a complete one.

## One-page reduction order

- 1. Shorten the narrative (assessment and supporting text)
- 2. Reduce competitors shown, never below 4
- 3. Drop the score breakdown, then the market map, then the claims-to-avoid line
- 4. Reduce the watchlist last — it is the highest-value section
- 5. Never overflow onto a second page
