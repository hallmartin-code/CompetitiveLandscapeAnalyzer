"""Company profile extraction, evidence grading and merge precedence."""

from __future__ import annotations

from extraction.company_extractor import (
    build_profile,
    extract_heuristic,
    extract_with_llm,
    merge_profiles,
)
from extraction.evidence import (
    grade_claim,
    has_citation,
    is_unevidenced_superlative,
    make_fact,
    merge_fact,
    normalise_locator,
    parse_evidence_type,
    parse_source_type,
)
from ingestion.normalizer import build_transcript, ingest
from models.schemas import (
    CompanyProfile,
    EvidenceType,
    Fact,
    SourceType,
    UserInputs,
)


# --------------------------------------------------------------------------
# Evidence primitives
# --------------------------------------------------------------------------


def test_locator_normalisation():
    assert normalise_locator("slide 7") == "Slide 7"
    assert normalise_locator("on Page #3 of the deck") == "Page 3"
    assert normalise_locator("") == ""


def test_source_and_evidence_type_parsing():
    assert parse_source_type("pitch_deck") is SourceType.PITCH_DECK
    assert parse_source_type("nonsense") is SourceType.UNKNOWN
    assert parse_evidence_type("company_claim") is EvidenceType.COMPANY_CLAIM
    assert parse_evidence_type(None) is EvidenceType.UNVERIFIED


def test_slide_locator_implies_the_deck_as_source():
    fact = make_fact("Austin, TX", locator="Slide 1", evidence_type="stated_fact")
    assert fact.sources[0].source_type is SourceType.PITCH_DECK
    assert fact.badge() == "DECK s1"


def test_inference_keeps_both_provenance_axes_consistent():
    """An inference must not render as an unknown source."""
    fact = make_fact("Healthcare IT", locator="analyst inference",
                     evidence_type="analyst_inference", confidence=0.55)
    assert fact.sources[0].source_type is SourceType.ANALYST_INFERENCE
    assert fact.badge() == "INF"


def test_nullish_values_produce_a_missing_fact():
    for value in (None, "", "Unknown", "N/A", "not disclosed", "  "):
        assert not make_fact(value).known


def test_confidence_is_clamped():
    assert make_fact("x", confidence=5.0).confidence == 1.0
    assert make_fact("x", confidence=-2.0).confidence == 0.0


# --------------------------------------------------------------------------
# Claim grading -- the core epistemic rule
# --------------------------------------------------------------------------


def test_uncited_deck_statement_is_a_company_claim():
    assert grade_claim("We are 10x faster", SourceType.PITCH_DECK) is EvidenceType.COMPANY_CLAIM


def test_cited_deck_statement_is_a_stated_fact():
    assert (
        grade_claim("13.7 hours per physician (AMA 2024 survey)", SourceType.PITCH_DECK)
        is EvidenceType.STATED_FACT
    )


def test_superlative_detection():
    assert is_unevidenced_superlative("KestrelPA is 10x faster than manual")
    assert is_unevidenced_superlative("The only solution purpose-built for clinics")
    assert is_unevidenced_superlative("Best-in-class industry-leading platform")
    # A superlative with a citation is not unevidenced.
    assert not is_unevidenced_superlative("10x faster per the AMA 2024 survey")
    # Plain factual statements are not superlatives.
    assert not is_unevidenced_superlative("We process 142,000 authorizations")


def test_citation_detection():
    assert has_citation("(AMA 2024)")
    assert has_citation("per Gartner")
    assert has_citation("see https://example.com/report")
    assert not has_citation("we believe it is better")


# --------------------------------------------------------------------------
# Merge precedence
# --------------------------------------------------------------------------


def test_user_input_outranks_the_deck():
    deck = make_fact("Acme Corp", locator="Slide 1", evidence_type="stated_fact", confidence=0.95)
    user = Fact.from_user("Acme Health Systems")
    assert merge_fact(deck, user).value == "Acme Health Systems"


def test_deck_outranks_inference():
    inference = make_fact("Healthcare", locator="analyst inference",
                          evidence_type="analyst_inference", confidence=0.99)
    deck = make_fact("Healthcare IT", locator="Slide 2",
                     evidence_type="stated_fact", confidence=0.5)
    assert merge_fact(inference, deck).value == "Healthcare IT"


def test_known_value_beats_missing():
    known = make_fact("Seed", locator="Slide 1")
    assert merge_fact(Fact.missing(), known).value == "Seed"
    assert merge_fact(known, Fact.missing()).value == "Seed"


def test_conflicting_values_are_recorded_not_hidden():
    """A disagreement between sources must stay auditable."""
    a = make_fact("$6M", locator="Slide 11", evidence_type="stated_fact", confidence=0.9)
    b = make_fact("$8M", locator="Slide 12", evidence_type="stated_fact", confidence=0.5)
    merged = merge_fact(a, b)
    assert merged.value == "$6M"
    assert any("Conflicting value: $8M" in s.excerpt for s in merged.sources)


# --------------------------------------------------------------------------
# Heuristic extraction
# --------------------------------------------------------------------------


def test_heuristic_extraction_finds_core_fields(sample_decks, diagnostics):
    result = ingest(sample_decks["pptx"])
    profile = extract_heuristic(result, diagnostics)
    assert profile.company_name.known
    assert "Kestrel" in profile.company_name.value
    assert profile.funding_stage.value == "Seed"
    assert profile.website.known
    assert profile.coverage > 0.2


def test_heuristic_extraction_never_invents(sample_decks, diagnostics):
    """Every heuristic value must carry a real locator."""
    result = ingest(sample_decks["pptx"])
    profile = extract_heuristic(result, diagnostics)
    for name in profile.known_fields():
        fact = profile.get(name)
        assert fact.sources, f"{name} has no source"
        assert fact.sources[0].source_type is not SourceType.UNKNOWN


def test_heuristic_on_empty_ingestion_returns_empty_profile(diagnostics):
    from models.schemas import IngestionResult

    profile = extract_heuristic(IngestionResult(), diagnostics)
    assert profile.known_fields() == []
    assert profile.coverage == 0.0


# --------------------------------------------------------------------------
# LLM-backed extraction (fake client)
# --------------------------------------------------------------------------


def test_llm_extraction_maps_fields_and_provenance(fake_llm, diagnostics):
    client = fake_llm(
        {
            "company-extraction": {
                "fields": [
                    {"field": "company_name", "value": "Kestrel", "source": "Slide 1",
                     "evidence_type": "stated_fact", "confidence": 0.95, "excerpt": ""},
                    {"field": "sector", "value": "Healthcare IT", "source": "analyst inference",
                     "evidence_type": "analyst_inference", "confidence": 0.5, "excerpt": ""},
                ],
                "notes": ["Competitor slide is thin."],
            }
        }
    )
    profile = extract_with_llm("transcript", client, diagnostics)
    assert profile.company_name.value == "Kestrel"
    assert profile.company_name.sources[0].source_type is SourceType.PITCH_DECK
    assert profile.sector.sources[0].source_type is SourceType.ANALYST_INFERENCE
    assert profile.extraction_notes == ["Competitor slide is thin."]


def test_llm_extraction_discards_unknown_field_names(fake_llm, diagnostics):
    client = fake_llm(
        {
            "company-extraction": {
                "fields": [
                    {"field": "made_up_field", "value": "x", "source": "Slide 1",
                     "evidence_type": "stated_fact", "confidence": 0.9, "excerpt": ""},
                ],
                "notes": [],
            }
        }
    )
    profile = extract_with_llm("transcript", client, diagnostics)
    assert profile.known_fields() == []
    assert any("unrecognised field names" in n for n in diagnostics.info)


def test_llm_extraction_failure_degrades_with_a_warning(fake_llm, diagnostics):
    client = fake_llm({"company-extraction": "model exploded"})
    assert extract_with_llm("transcript", client, diagnostics) is None
    assert any("fell back to heuristics" in w for w in diagnostics.warnings)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def test_build_profile_without_a_model_warns_and_degrades(sample_decks, settings, diagnostics):
    from llm.client import LLMClient

    result = ingest(sample_decks["pptx"])
    transcript, _ = build_transcript(result, settings.max_transcript_chars)
    client = LLMClient(settings)
    assert not client.available          # the offline fixture guarantees this

    profile = build_profile(result, transcript, UserInputs(), client, diagnostics, settings)
    assert diagnostics.degraded
    assert any("without a language model" in w for w in diagnostics.warnings)
    assert profile.company_name.known    # heuristics still recovered something


def test_user_overrides_are_applied_last(sample_decks, settings, diagnostics):
    from llm.client import LLMClient

    result = ingest(sample_decks["pptx"])
    transcript, _ = build_transcript(result, settings.max_transcript_chars)
    user = UserInputs(company_name="Renamed Co", sector="Fintech")
    profile = build_profile(
        result, transcript, user, LLMClient(settings), diagnostics, settings
    )
    assert profile.company_name.value == "Renamed Co"
    assert profile.sector.value == "Fintech"
    assert profile.company_name.sources[0].source_type is SourceType.USER_PROVIDED


def test_merge_profiles_combines_notes_without_duplicates():
    a, b = CompanyProfile(), CompanyProfile()
    a.extraction_notes = ["shared", "only-a"]
    b.extraction_notes = ["shared", "only-b"]
    merged = merge_profiles(a, b)
    assert merged.extraction_notes == ["shared", "only-a", "only-b"]


def test_missing_company_name_is_warned(settings, diagnostics):
    """A nameless report is a real problem the analyst must be told about."""
    from llm.client import LLMClient
    from models.schemas import IngestionResult, SourceDocument

    empty = IngestionResult(primary=SourceDocument(name="x.pdf", doc_type="pdf"))
    build_profile(empty, "", UserInputs(), LLMClient(settings), diagnostics, settings)
    assert any("Company name could not be determined" in w for w in diagnostics.warnings)
