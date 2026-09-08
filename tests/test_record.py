"""The structured TEN Capital JSON record, and its transmission gate."""

from __future__ import annotations

import json

from models.schemas import NOT_DISCLOSED, Competitor, Consent, ThreatLevel
from reporting.record import SCHEMA_VERSION, build_record, transmit, write_record


# --------------------------------------------------------------------------
# Schema shape
# --------------------------------------------------------------------------


def test_record_has_every_top_level_key_from_the_spec(analysis):
    record = build_record(analysis)
    for key in (
        "user", "company", "analysis", "competitors", "whitespace_opportunities",
        "risks", "diligence_questions", "consent", "analysis_timestamp",
    ):
        assert key in record, f"missing key: {key}"
    assert record["schema_version"] == SCHEMA_VERSION


def test_company_block_matches_the_spec(analysis):
    company = build_record(analysis)["company"]
    assert set(company) == {"name", "website", "sector", "subsector", "stage"}
    assert company["name"] == "Kestrel Authorization Systems"


def test_analysis_block_matches_the_spec(analysis):
    block = build_record(analysis)["analysis"]
    for key in (
        "differentiation_score", "confidence_score", "competitive_position",
        "recommended_positioning", "competitor_count",
    ):
        assert key in block
    assert isinstance(block["differentiation_score"], int)
    assert 0 <= block["differentiation_score"] <= 100
    assert 0.0 <= block["confidence_score"] <= 1.0
    assert block["competitor_count"] == len(analysis.competitors)


def test_competitor_entries_match_the_spec(analysis):
    entry = build_record(analysis)["competitors"][0]
    for key in (
        "name", "category", "target_customer", "product", "pricing", "traction",
        "funding", "distribution", "strengths", "weaknesses", "threat_level", "sources",
    ):
        assert key in entry, f"missing competitor key: {key}"
    assert entry["threat_level"] in {"low", "medium", "high"}
    assert entry["category"] in {"direct", "indirect", "incumbent", "emerging", "substitute"}
    assert isinstance(entry["strengths"], list)


def test_record_is_json_serialisable(analysis, tmp_path):
    path = write_record(analysis, tmp_path / "record.json")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["company"]["name"] == "Kestrel Authorization Systems"
    assert loaded["analysis"]["differentiation_score"] == analysis.score.total


def test_record_round_trips_without_loss(analysis, tmp_path):
    path = write_record(analysis, tmp_path / "record.json")
    assert json.loads(path.read_text(encoding="utf-8")) == build_record(analysis)


# --------------------------------------------------------------------------
# Provenance is preserved in full
# --------------------------------------------------------------------------


def test_every_profile_field_carries_its_provenance(analysis):
    """The record is the audit trail the one-pager only hints at."""
    profile = build_record(analysis)["company_profile"]
    assert len(profile) == 34
    for name, field in profile.items():
        assert set(field) == {"value", "evidence_type", "confidence", "sources"}
        if field["value"] is not None:
            assert field["evidence_type"] != "missing", name


def test_profile_sources_include_slide_locators(analysis):
    field = build_record(analysis)["company_profile"]["company_name"]
    assert field["sources"][0]["source_type"] == "PITCH_DECK"
    assert field["sources"][0]["locator"] == "Slide 1"


def test_gaps_are_recorded_explicitly(analysis):
    """Absence of data is an output, not an omission."""
    record = build_record(analysis)
    assert record["profile_gaps"]
    assert "valuation" in record["profile_gaps"]


def test_discounted_claims_are_recorded(analysis):
    discounted = build_record(analysis)["analysis"]["unevidenced_claims_discounted"]
    assert any("10x faster" in x for x in discounted)


def test_risks_and_questions_stay_aligned(analysis):
    """The spec keeps them as separate keys; they must not drift apart."""
    record = build_record(analysis)
    assert len(record["risks"]) == len(record["diligence_questions"])
    for risk, question in zip(record["risks"], record["diligence_questions"]):
        assert risk["concern"] == question["relates_to"]
        assert risk["severity"] == question["severity"]


def test_run_diagnostics_are_recorded(analysis, tmp_path, settings):
    from reporting.pdf_generator import generate_pdf

    generate_pdf(analysis, tmp_path / "r.pdf", settings)
    run = build_record(analysis)["run"]
    assert run["llm_available"] is True
    assert run["research_performed"] is True
    assert isinstance(run["warnings"], list)


# --------------------------------------------------------------------------
# No fabrication
# --------------------------------------------------------------------------


def test_unknown_competitor_data_is_recorded_as_undisclosed(analysis):
    blank = Competitor(name="Mystery Co", threat_level=ThreatLevel.MEDIUM)
    analysis.competitors = [blank]
    entry = build_record(analysis)["competitors"][0]
    for key in ("pricing", "revenue", "funding", "valuation", "traction"):
        assert entry[key] == NOT_DISCLOSED, key
    assert entry["investors"] == []


# --------------------------------------------------------------------------
# Consent and transmission
# --------------------------------------------------------------------------


def test_consent_state_is_recorded(analysis):
    consent = build_record(analysis)["consent"]
    assert consent["status"] == "granted"
    assert consent["public_research_permitted"] is True
    assert consent["external_transmission_permitted"] is False
    assert consent["timestamp"]


def test_transmission_refused_without_user_authorisation(analysis, settings):
    settings.crm_endpoint = "https://example.invalid/api"
    analysis.consent.external_transmission_permitted = False
    sent, message = transmit(analysis, settings)
    assert sent is False
    assert "did not authorise" in message


def test_transmission_refused_without_a_configured_endpoint(analysis, settings):
    analysis.consent.external_transmission_permitted = True
    settings.crm_endpoint = None
    sent, message = transmit(analysis, settings)
    assert sent is False
    assert "no ten_crm_endpoint" in message.lower()


def test_transmission_failure_is_returned_not_raised(analysis, settings):
    """A network failure must not take down a completed analysis."""
    analysis.consent.external_transmission_permitted = True
    settings.crm_endpoint = "http://127.0.0.1:9/unreachable"
    sent, message = transmit(analysis, settings, timeout=1.0)
    assert sent is False
    assert "Transmission failed" in message


def test_writing_to_disk_is_not_transmission(analysis, tmp_path, settings):
    """A local write must never require or imply transmission consent."""
    analysis.consent = Consent()          # nothing granted at all
    path = write_record(analysis, tmp_path / "record.json")
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["consent"]["status"] == "not_provided"
