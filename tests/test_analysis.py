"""Competitor classification, differentiation scoring, and the analysis modules."""

from __future__ import annotations


from analysis.competitor_classifier import (
    canonical,
    deduplicate,
    filter_weak,
    select,
)
from analysis.differentiation import _BASELINE, compute_score
from config import ALL_DIMENSIONS, SCORE_WEIGHTS, Settings, score_band
from models.schemas import (
    Advantage,
    CompanyProfile,
    CompetitorCategory,
    DimensionAssessment,
    EvidenceType,
    RunDiagnostics,
    ThreatLevel,
)
from tests.conftest import make_competitor


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def test_canonical_strips_corporate_noise():
    assert canonical("Availity, LLC") == canonical("Availity")
    assert canonical("Humata Health Inc.") == canonical("Humata")


def test_deduplicate_merges_the_same_company():
    a = make_competitor("Optum", threat=ThreatLevel.MEDIUM)
    b = make_competitor("Optum / Change Healthcare", threat=ThreatLevel.HIGH)
    b.funding_raised = "$1B"
    kept, removed = deduplicate([a, b])
    assert removed == 1
    assert len(kept) == 1
    # The merge keeps the more severe threat and the real datum.
    assert kept[0].threat_level is ThreatLevel.HIGH
    assert kept[0].funding_raised == "$1B"


def test_deduplicate_keeps_genuinely_different_companies():
    kept, removed = deduplicate(
        [make_competitor("Availity"), make_competitor("Waystar"), make_competitor("Rhyme")]
    )
    assert removed == 0 and len(kept) == 3


def test_short_names_are_not_treated_as_substrings():
    """'Tennr' must not swallow 'Tenner Health' on a 5-character overlap."""
    kept, _ = deduplicate([make_competitor("Ada"), make_competitor("Adaptive Systems")])
    assert len(kept) == 2


def test_filter_drops_speculative_competitors():
    weak = make_competitor("Maybe Co", relevance=0.1, confidence=0.1)
    strong = make_competitor("Availity", relevance=0.9, confidence=0.8)
    kept, dropped = filter_weak([weak, strong])
    assert [c.name for c in kept] == ["Availity"]
    assert dropped and "Maybe Co" in dropped[0]


def test_deck_named_competitors_are_always_kept():
    """The investor has seen the deck's competitor slide; never silently omit one."""
    named = make_competitor("Availity", relevance=0.05, confidence=0.05, named_in_deck=True)
    kept, _ = filter_weak([named])
    assert [c.name for c in kept] == ["Availity"]


def test_select_warns_when_no_substitute_exists():
    diagnostics = RunDiagnostics()
    select([make_competitor("Availity"), make_competitor("Waystar")], diagnostics)
    assert any("substitute" in w.lower() for w in diagnostics.warnings)


def test_select_preserves_category_coverage_when_trimming():
    """Trimming to fit must not delete the only substitute or the only incumbent."""
    settings = Settings()
    settings.max_competitors = 5
    many = [
        make_competitor(f"Direct {i}", CompetitorCategory.DIRECT, ThreatLevel.HIGH, 0.9, 0.9)
        for i in range(8)
    ] + [
        make_competitor("Status quo", CompetitorCategory.SUBSTITUTE, ThreatLevel.LOW, 0.3, 0.3),
        make_competitor("Big Inc", CompetitorCategory.INCUMBENT, ThreatLevel.LOW, 0.3, 0.3),
    ]
    kept = select(many, RunDiagnostics(), settings)
    assert len(kept) == 5
    categories = {c.category for c in kept}
    assert CompetitorCategory.SUBSTITUTE in categories
    assert CompetitorCategory.INCUMBENT in categories


def test_select_warns_on_a_thin_competitive_set():
    settings = Settings()
    settings.min_competitors = 4
    diagnostics = RunDiagnostics()
    select([make_competitor("Only One")], diagnostics, settings)
    assert any("relevance threshold" in w for w in diagnostics.warnings)


def test_select_on_empty_input_warns():
    diagnostics = RunDiagnostics()
    assert select([], diagnostics) == []
    assert any("No competitors" in w for w in diagnostics.warnings)


def test_priority_ranks_threat_and_relevance():
    high = make_competitor("High", threat=ThreatLevel.HIGH, relevance=0.9)
    low = make_competitor("Low", threat=ThreatLevel.LOW, relevance=0.2)
    assert high.priority > low.priority


# --------------------------------------------------------------------------
# Differentiation scoring
# --------------------------------------------------------------------------


def test_weights_sum_to_one_hundred():
    assert sum(w for _, w in SCORE_WEIGHTS) == 100


def test_all_eighteen_dimensions_are_grouped():
    assert len(ALL_DIMENSIONS) == 18
    assert len(set(ALL_DIMENSIONS)) == 18


def _uniform(advantage, evidence, sustainability="medium", confidence=0.9):
    return [
        DimensionAssessment(
            dimension=name, advantage=advantage, evidence_type=evidence,
            confidence=confidence, sustainability=sustainability, evidence="e",
        )
        for name in ALL_DIMENSIONS
    ]


def test_evidenced_dominance_scores_highly(profile, competitors):
    score = compute_score(
        _uniform(Advantage.COMPANY, EvidenceType.STATED_FACT, "high"),
        profile, competitors, RunDiagnostics(),
    )
    assert score.total >= 90
    assert score.band == "Highly differentiated"


def test_evidenced_disadvantage_scores_low(profile, competitors):
    score = compute_score(
        _uniform(Advantage.COMPETITOR, EvidenceType.STATED_FACT, "low"),
        profile, competitors, RunDiagnostics(),
    )
    assert score.total <= 20
    assert score.band == "Commodity / highly vulnerable"


def test_unevidenced_claims_do_not_earn_points(profile, competitors):
    """The spec's central scoring rule, stated as a test.

    A company claiming advantage everywhere, with no evidence, must not
    outscore one that simply established nothing.
    """
    claimed = compute_score(
        _uniform(Advantage.COMPANY, EvidenceType.COMPANY_CLAIM, "high"),
        profile, competitors, RunDiagnostics(),
    )
    unknown = compute_score(
        _uniform(Advantage.UNKNOWN, EvidenceType.MISSING, "unknown"),
        profile, competitors, RunDiagnostics(),
    )
    assert claimed.total - unknown.total < 25, (
        "unevidenced claims are earning too many points over an empty assessment"
    )
    assert claimed.unevidenced_claims_discounted


def test_evidence_quality_changes_the_score(profile, competitors):
    """The same claimed advantage scores higher when it is actually evidenced."""
    fact = compute_score(
        _uniform(Advantage.COMPANY, EvidenceType.STATED_FACT, "high"),
        profile, competitors, RunDiagnostics(),
    )
    claim = compute_score(
        _uniform(Advantage.COMPANY, EvidenceType.COMPANY_CLAIM, "high"),
        profile, competitors, RunDiagnostics(),
    )
    assert fact.total > claim.total + 20


def test_unknown_assessment_lands_near_the_baseline(profile, competitors):
    score = compute_score(
        _uniform(Advantage.UNKNOWN, EvidenceType.MISSING),
        profile, competitors, RunDiagnostics(),
    )
    assert abs(score.total - _BASELINE * 100) < 8


def test_score_is_bounded(profile, competitors):
    for dims in (
        _uniform(Advantage.COMPANY, EvidenceType.STATED_FACT, "high", 1.0),
        _uniform(Advantage.COMPETITOR, EvidenceType.STATED_FACT, "low", 1.0),
    ):
        score = compute_score(dims, profile, competitors, RunDiagnostics())
        assert 0 <= score.total <= 100
        assert 0.0 <= score.confidence <= 1.0


def test_category_weights_are_reported_intact(profile, competitors, dimensions):
    score = compute_score(dimensions, profile, competitors, RunDiagnostics())
    assert len(score.categories) == len(SCORE_WEIGHTS)
    assert sum(c.weight for c in score.categories) == 100
    for cat in score.categories:
        assert 0.0 <= cat.raw <= 1.0
        assert abs(cat.weighted - cat.raw * cat.weight) < 0.02


def test_sustainability_category_reflects_durability(profile, competitors):
    durable = compute_score(
        _uniform(Advantage.COMPANY, EvidenceType.STATED_FACT, "high"),
        profile, competitors, RunDiagnostics(),
    )
    fragile = compute_score(
        _uniform(Advantage.COMPANY, EvidenceType.STATED_FACT, "low"),
        profile, competitors, RunDiagnostics(),
    )
    def sustain(s):
        return next(c.raw for c in s.categories if c.name == "Sustainability of Advantage")

    assert sustain(durable) > sustain(fragile) + 0.3


def test_degraded_run_lowers_confidence(profile, competitors, dimensions):
    normal = compute_score(dimensions, profile, competitors, RunDiagnostics(research_performed=True))
    degraded_diag = RunDiagnostics(degraded=True)
    degraded = compute_score(dimensions, profile, competitors, degraded_diag)
    assert degraded.confidence < normal.confidence


def test_uncited_superlative_is_listed_as_discounted(profile, competitors, dimensions):
    """The company's own '10x faster' claim must be named in the discount list."""
    score = compute_score(dimensions, profile, competitors, RunDiagnostics())
    assert any("10x faster" in x for x in score.unevidenced_claims_discounted)


def test_score_bands_match_the_specification():
    assert score_band(100) == "Highly differentiated"
    assert score_band(80) == "Highly differentiated"
    assert score_band(79) == "Meaningfully differentiated"
    assert score_band(65) == "Meaningfully differentiated"
    assert score_band(64) == "Moderate differentiation"
    assert score_band(50) == "Moderate differentiation"
    assert score_band(49) == "Weak differentiation"
    assert score_band(35) == "Weak differentiation"
    assert score_band(34) == "Commodity / highly vulnerable"
    assert score_band(0) == "Commodity / highly vulnerable"


# --------------------------------------------------------------------------
# Downstream analysis modules, offline
# --------------------------------------------------------------------------


def test_whitespace_is_skipped_without_competitors(profile, fake_llm):
    from analysis.whitespace import analyse

    diagnostics = RunDiagnostics()
    assert analyse(profile, [], fake_llm(), diagnostics) == []
    assert any("no competitive set" in n.lower() for n in diagnostics.info)


def test_market_map_needs_at_least_two_competitors(profile, fake_llm):
    from analysis.market_map import build

    diagnostics = RunDiagnostics()
    assert build(profile, [make_competitor("Solo")], fake_llm(), diagnostics) is None


def test_market_map_fallback_places_the_company(profile, competitors, fake_llm):
    from analysis.market_map import build

    diagnostics = RunDiagnostics()
    market_map = build(profile, competitors, fake_llm(available=False), diagnostics)
    assert market_map is not None
    assert any(e.is_company for e in market_map.entries)
    assert all(0.0 <= e.x <= 1.0 and 0.0 <= e.y <= 1.0 for e in market_map.entries)


def test_investor_concerns_fallback_derives_from_gaps(competitors, dimensions, fake_llm):
    """With no model, concerns must come from what the deck failed to establish."""
    from analysis.investor_risks import identify
    from models.schemas import DifferentiationScore

    bare = CompanyProfile()          # nothing established at all
    concerns = identify(
        bare, competitors, dimensions, DifferentiationScore(),
        fake_llm(available=False), RunDiagnostics(),
    )
    assert concerns
    for c in concerns:
        # The three fields must stay distinct and populated.
        assert c.current_risk and c.diligence_question and c.evidence_needed
        assert c.current_risk != c.diligence_question
        assert not c.current_risk.endswith("?")


def test_positioning_fallback_marks_gaps_instead_of_inventing(competitors, fake_llm):
    from analysis.positioning import recommend
    from models.schemas import DifferentiationScore

    bare = CompanyProfile()
    positioning = recommend(
        bare, competitors, [], DifferentiationScore(), fake_llm(available=False), RunDiagnostics()
    )
    assert "not established" in positioning.recommended
    assert positioning.word_count > 0


def test_positioning_fallback_stays_a_positioning_line(profile, competitors, fake_llm):
    """Verbose source fields must be compressed, not concatenated wholesale."""
    from analysis.positioning import recommend
    from models.schemas import DifferentiationScore, Fact

    verbose = profile.model_copy(deep=True)
    verbose.core_product = Fact(value=" ".join(["word"] * 200), confidence=0.5)
    positioning = recommend(
        verbose, competitors, [], DifferentiationScore(),
        fake_llm(available=False), RunDiagnostics(),
    )
    assert positioning.word_count < 60


def test_positioning_word_count_is_checked(profile, competitors, fake_llm):
    from analysis.positioning import recommend
    from models.schemas import DifferentiationScore

    client = fake_llm(
        {
            "positioning": {
                "current_implied": "Vague.",
                "recommended": "Too short.",
                "proof_points_required": ["A metric"],
                "claims_to_avoid": ["A claim"],
                "competitive_narrative": "A narrative.",
            }
        }
    )
    diagnostics = RunDiagnostics()
    positioning = recommend(
        profile, competitors, [], DifferentiationScore(), client, diagnostics
    )
    assert positioning.word_count == 2
    assert any("words (target" in n for n in diagnostics.info)


def test_positioning_always_surfaces_the_uncited_superlative(profile, competitors, fake_llm):
    from analysis.positioning import recommend
    from models.schemas import DifferentiationScore

    client = fake_llm(
        {
            "positioning": {
                "current_implied": "Vague.",
                "recommended": " ".join(["word"] * 25),
                "proof_points_required": [],
                "claims_to_avoid": ["Something entirely unrelated about pricing"],
                "competitive_narrative": "",
            }
        }
    )
    positioning = recommend(
        profile, competitors, [], DifferentiationScore(), client, RunDiagnostics()
    )
    assert any("10x faster" in c for c in positioning.claims_to_avoid)
