"""Competitor discovery fallbacks and enrichment's no-fabrication guards."""

from __future__ import annotations

from analysis.competitor_classifier import canonical
from models.schemas import (
    NOT_DISCLOSED,
    UNKNOWN,
    CompanyProfile,
    RunDiagnostics,
)
from research.competitor_discovery import _from_deck_only, _plausible_names
from research.competitor_enrichment import _match, _sanitise, enrich
from tests.conftest import deck_fact, make_competitor


# --------------------------------------------------------------------------
# No fabrication -- the sentinel rules
# --------------------------------------------------------------------------


def test_missing_values_become_the_right_sentinel():
    assert _sanitise(None, "revenue") == NOT_DISCLOSED
    assert _sanitise("", "headquarters") == UNKNOWN
    assert _sanitise("unknown", "pricing") == NOT_DISCLOSED
    assert _sanitise("n/a", "founded_year") == UNKNOWN


def test_hedged_financials_are_rejected():
    """A hedged figure is a guess wearing a hedge."""
    for value in (
        "approximately $50M",
        "around 200 customers",
        "~$1.2B valuation",
        "reportedly raised $30M",
        "estimated $10M ARR",
        "believed to be $5M",
        "$10M venture round reported by press",
    ):
        assert _sanitise(value, "funding_raised") == NOT_DISCLOSED, value


def test_sourced_financials_are_kept():
    assert _sanitise("$200M total per PitchBook", "funding_raised") == "$200M total per PitchBook"
    assert _sanitise("Publicly listed (Nasdaq: WAY)", "funding_raised").startswith("Publicly")


def test_hedging_is_allowed_in_qualitative_fields():
    """Hedging a qualitative read is honest; hedging a number is invention."""
    assert _sanitise("roughly enterprise-focused", "headquarters") != UNKNOWN


def test_internal_vocabulary_is_stripped_from_values():
    assert _sanitise("company_claim: 30,000 clients", "traction") == "30,000 clients"
    assert _sanitise("stated_fact — Boston, MA", "headquarters") == "Boston, MA"


def test_a_value_that_is_only_jargon_becomes_a_sentinel():
    assert _sanitise("company_claim", "revenue") == NOT_DISCLOSED


# --------------------------------------------------------------------------
# Enrichment record matching
# --------------------------------------------------------------------------


def test_match_resolves_exact_and_shortened_names():
    names = ["Availity", "Infinx Healthcare", "In-house authorization staff working portals"]
    by_key = {canonical(n): n for n in names}
    assert _match("Availity, LLC", by_key) == "Availity"
    assert _match("Infinx", by_key) == "Infinx Healthcare"
    assert _match("In-house authorization staff", by_key).startswith("In-house")


def test_match_refuses_unrelated_and_ambiguous_names():
    by_key = {canonical(n): n for n in ["Availity", "Waystar"]}
    assert _match("Totally Unrelated Corp", by_key) is None
    assert _match("", by_key) is None


def test_enrichment_without_a_model_leaves_sentinels_intact(profile, fake_llm, diagnostics):
    from research.source_manager import SourceManager
    from models.schemas import Consent

    competitors = [make_competitor("Availity")]
    sources = SourceManager(Consent(), diagnostics)
    out = enrich(competitors, profile, fake_llm(available=False), sources, diagnostics)
    assert out[0].funding_raised == NOT_DISCLOSED
    assert any("no language model" in n for n in diagnostics.info)


def test_unmatched_enrichment_records_are_reported(profile, fake_llm, diagnostics, settings):
    """Silently discarding a record would be an invisible quality loss."""
    from research.source_manager import SourceManager
    from models.schemas import Consent

    client = fake_llm(
        {
            "competitor-enrichment": {
                "competitors": [
                    {
                        "name": "Some Company Nobody Discovered", "website": "Unknown",
                        "headquarters": "Unknown", "founded_year": "Unknown",
                        "pricing": NOT_DISCLOSED, "revenue": NOT_DISCLOSED,
                        "traction": NOT_DISCLOSED, "customer_count": NOT_DISCLOSED,
                        "user_count": NOT_DISCLOSED, "funding_raised": NOT_DISCLOSED,
                        "latest_round": NOT_DISCLOSED, "valuation": NOT_DISCLOSED,
                        "investors": [], "partnerships": NOT_DISCLOSED,
                        "source_urls": [], "confidence": 0.5,
                    }
                ],
                "unresolved": [],
            }
        }
    )
    sources = SourceManager(Consent(), diagnostics, settings)
    enrich([make_competitor("Availity")], profile, client, sources, diagnostics, settings)
    assert any("could not be matched" in w for w in diagnostics.warnings)
    assert any("No competitor factual detail" in w for w in diagnostics.warnings)


def test_enrichment_does_not_overwrite_discovered_qualitative_data(
    profile, fake_llm, diagnostics, settings
):
    from research.source_manager import SourceManager
    from models.schemas import Consent

    competitor = make_competitor("Availity")
    competitor.headquarters = "Jacksonville, FL"
    client = fake_llm(
        {
            "competitor-enrichment": {
                "competitors": [
                    {
                        "name": "Availity", "website": "https://availity.com",
                        "headquarters": "Somewhere Else", "founded_year": "2001",
                        "pricing": NOT_DISCLOSED, "revenue": NOT_DISCLOSED,
                        "traction": NOT_DISCLOSED, "customer_count": NOT_DISCLOSED,
                        "user_count": NOT_DISCLOSED, "funding_raised": "$200M per PitchBook",
                        "latest_round": NOT_DISCLOSED, "valuation": NOT_DISCLOSED,
                        "investors": ["Novo Holdings"], "partnerships": NOT_DISCLOSED,
                        "source_urls": ["https://www.crunchbase.com/organization/availity"],
                        "confidence": 0.8,
                    }
                ],
                "unresolved": [],
            }
        }
    )
    sources = SourceManager(Consent(), diagnostics, settings)
    out = enrich([competitor], profile, client, sources, diagnostics, settings)[0]

    assert out.headquarters == "Jacksonville, FL"     # existing value preserved
    assert out.founded_year == "2001"                 # sentinel filled in
    assert out.funding_raised == "$200M per PitchBook"
    assert out.investors == ["Novo Holdings"]
    assert any(s.url and "crunchbase" in s.url for s in out.sources)


# --------------------------------------------------------------------------
# Deck-only discovery fallback
# --------------------------------------------------------------------------


def test_plausible_names_extracts_from_a_bracketed_list():
    raw = "Legacy clearinghouses (Availity, Waystar) — transaction plumbing"
    assert _plausible_names(raw) == ["Legacy clearinghouses", "Availity", "Waystar"]


def test_plausible_names_handles_a_simple_list():
    assert _plausible_names("Availity, Waystar, and Cohere Health") == [
        "Availity", "Waystar", "Cohere Health"
    ]


def test_plausible_names_rejects_prose():
    """A sentence fragment in a competitor column is worse than no row."""
    assert _plausible_names("We compete with nobody because our approach is novel") == []
    assert _plausible_names("far easier to adopt than the manual process") == []


def test_deck_only_fallback_warns_when_nothing_is_parseable():
    profile = CompanyProfile()
    profile.known_competitors = deck_fact("we are the only solution that does this", "Slide 8")
    diagnostics = RunDiagnostics()
    assert _from_deck_only(profile, diagnostics) == []
    assert any("could not be parsed" in w for w in diagnostics.warnings)


def test_deck_only_fallback_flags_that_it_is_not_an_analysis():
    profile = CompanyProfile()
    profile.known_competitors = deck_fact("Availity, Waystar", "Slide 8")
    diagnostics = RunDiagnostics()
    competitors = _from_deck_only(profile, diagnostics)
    assert [c.name for c in competitors] == ["Availity", "Waystar"]
    assert all(c.named_in_deck for c in competitors)
    assert all(c.confidence <= 0.3 for c in competitors)
    assert any("No independent discovery" in w for w in diagnostics.warnings)


def test_deck_only_fallback_with_no_competitor_field_warns():
    diagnostics = RunDiagnostics()
    assert _from_deck_only(CompanyProfile(), diagnostics) == []
    assert any("deck names none" in w for w in diagnostics.warnings)
