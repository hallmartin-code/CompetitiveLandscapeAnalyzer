"""
Automated report delivery.

Every test here runs offline: the Resend endpoint is monkeypatched, so no test
can send a real email or depend on network conditions. What is asserted is the
payload the app *would* send, which is where the interesting mistakes live.
"""

from __future__ import annotations

import base64

from config import Settings
from notifications.email_sender import (
    RESEND_ENDPOINT,
    build_html,
    build_text,
    send_report,
    subject_line,
)


#: A Resend-shaped token, assembled at runtime rather than written as a
#: literal. Secret scanners (GitHub push protection among them) match on
#: committed text, and a test fixture is not worth a blocked push or a
#: false-positive alert. It is still key-shaped once built, which is what the
#: redaction test needs.
FAKE_RESEND_KEY = "re_" + ("A1b2C3d4" * 3)


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"id": "abc123def456"}
        self.text = text

    def json(self):
        return self._payload


def _capture(monkeypatch, response=None):
    """Intercept the outbound POST and return the captured request."""
    captured = {}

    # The parameter must be named `json` -- that is the keyword the sender
    # passes -- so the module is deliberately not imported here.
    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return response or _Response()

    monkeypatch.setattr("httpx2.post", fake_post, raising=False)
    monkeypatch.setattr("httpx.post", fake_post, raising=False)
    return captured


def _configured(tmp_path) -> Settings:
    s = Settings()
    s.output_dir = tmp_path
    s.resend_api_key = FAKE_RESEND_KEY
    s.email_enabled = True
    s.email_to = ["Info@tencapital.group"]
    s.email_from = "TEN Capital <reports@tencapital.group>"
    return s


# --------------------------------------------------------------------------
# Gating
# --------------------------------------------------------------------------


def test_not_sent_without_an_api_key(analysis, tmp_path):
    s = _configured(tmp_path)
    s.resend_api_key = None
    sent, message = send_report(analysis, settings=s)
    assert sent is False
    assert "RESEND_API_KEY" in message


def test_not_sent_when_disabled(analysis, tmp_path):
    s = _configured(tmp_path)
    s.email_enabled = False
    sent, message = send_report(analysis, settings=s)
    assert sent is False
    assert "disabled" in message


def test_not_sent_without_a_recipient(analysis, tmp_path):
    s = _configured(tmp_path)
    s.email_to = []
    sent, message = send_report(analysis, settings=s)
    assert sent is False
    assert "recipient" in message


def test_email_configured_property(tmp_path):
    s = _configured(tmp_path)
    assert s.email_configured
    s.resend_api_key = None
    assert not s.email_configured


# --------------------------------------------------------------------------
# Payload
# --------------------------------------------------------------------------


def test_payload_addresses_the_configured_recipient(analysis, tmp_path, monkeypatch):
    s = _configured(tmp_path)
    captured = _capture(monkeypatch)
    sent, _ = send_report(analysis, settings=s)

    assert sent is True
    assert captured["url"] == RESEND_ENDPOINT
    body = captured["json"]
    assert body["to"] == ["Info@tencapital.group"]
    assert body["from"] == "TEN Capital <reports@tencapital.group>"


def test_authorization_header_carries_the_key(analysis, tmp_path, monkeypatch):
    s = _configured(tmp_path)
    captured = _capture(monkeypatch)
    send_report(analysis, settings=s)
    assert captured["headers"]["Authorization"] == f"Bearer {FAKE_RESEND_KEY}"


def test_subject_carries_company_and_verdict(analysis):
    subject = subject_line(analysis)
    assert "Kestrel Authorization Systems" in subject
    assert f"{analysis.score.total}/100" in subject
    assert analysis.score.band in subject


def test_subject_flags_a_degraded_run(analysis):
    analysis.diagnostics.degraded = True
    assert "DEGRADED RUN" in subject_line(analysis)


def test_payload_includes_both_alternatives(analysis, tmp_path, monkeypatch):
    """A text/plain alternative is required for deliverability, not optional."""
    s = _configured(tmp_path)
    captured = _capture(monkeypatch)
    send_report(analysis, settings=s)
    body = captured["json"]
    assert body["html"].lstrip().startswith("<!doctype html>")
    assert "COMPETITIVE INTELLIGENCE" in body["text"]


def test_attachments_are_base64_encoded(analysis, tmp_path, monkeypatch, settings):
    from pipeline import render

    pdf_path, json_path = render(analysis, tmp_path, settings=settings)
    s = _configured(tmp_path)
    captured = _capture(monkeypatch)
    send_report(analysis, pdf_path, json_path, settings=s)

    attachments = captured["json"]["attachments"]
    assert len(attachments) == 2
    names = {a["filename"] for a in attachments}
    assert names == {pdf_path.name, json_path.name}
    for item in attachments:
        decoded = base64.b64decode(item["content"])
        assert len(decoded) > 0
    # The PDF attachment must actually be the PDF.
    pdf_item = next(a for a in attachments if a["filename"].endswith(".pdf"))
    assert base64.b64decode(pdf_item["content"]).startswith(b"%PDF")


def test_attachments_can_be_switched_off(analysis, tmp_path, monkeypatch, settings):
    from pipeline import render

    pdf_path, json_path = render(analysis, tmp_path, settings=settings)
    s = _configured(tmp_path)
    s.email_attach_pdf = False
    s.email_attach_json = False
    captured = _capture(monkeypatch)
    send_report(analysis, pdf_path, json_path, settings=s)
    assert "attachments" not in captured["json"]


def test_missing_attachment_file_does_not_break_the_send(analysis, tmp_path, monkeypatch):
    s = _configured(tmp_path)
    captured = _capture(monkeypatch)
    sent, _ = send_report(analysis, tmp_path / "gone.pdf", tmp_path / "gone.json", settings=s)
    assert sent is True
    assert "attachments" not in captured["json"]


# --------------------------------------------------------------------------
# Body content
# --------------------------------------------------------------------------


def test_html_carries_the_decision_relevant_findings(analysis, tmp_path):
    html = build_html(analysis, _configured(tmp_path))
    assert "Kestrel Authorization Systems" in html
    assert str(analysis.score.total) in html
    assert analysis.score.band in html
    assert "RECOMMENDED POSITIONING" in html
    assert "Investor watchlist" in html
    # Each concern's three fields must survive into the email.
    assert "Risk:" in html and "Ask:" in html and "Needs:" in html
    assert "Confidential" in html


def test_text_alternative_carries_the_same_findings(analysis, tmp_path):
    text = build_text(analysis, _configured(tmp_path))
    assert "Kestrel Authorization Systems" in text
    assert f"{analysis.score.total}/100" in text
    assert "RECOMMENDED POSITIONING" in text
    assert "INVESTOR WATCHLIST" in text


def test_degraded_run_is_flagged_in_the_body(analysis, tmp_path):
    analysis.diagnostics.degraded = True
    s = _configured(tmp_path)
    assert "Degraded run" in build_html(analysis, s)
    assert "DEGRADED RUN" in build_text(analysis, s)


def test_html_escapes_content(tmp_path, analysis):
    """Extracted text is untrusted input; it must not be able to inject markup."""
    from models.schemas import Fact

    analysis.profile.company_name = Fact(value="<script>alert(1)</script>", confidence=0.9)
    html = build_html(analysis, _configured(tmp_path))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_no_competitors_does_not_break_the_body(analysis, tmp_path):
    analysis.competitors = []
    analysis.concerns = []
    analysis.whitespace = []
    s = _configured(tmp_path)
    assert "No competitors identified" in build_html(analysis, s)
    assert build_text(analysis, s)


# --------------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------------


def test_api_error_is_reported_not_raised(analysis, tmp_path, monkeypatch):
    s = _configured(tmp_path)
    _capture(monkeypatch, _Response(422, {"message": "Invalid `from` field."}))
    sent, message = send_report(analysis, settings=s)
    assert sent is False
    assert "422" in message and "Invalid `from` field" in message
    assert any("Email delivery failed" in w for w in analysis.diagnostics.warnings)


def test_network_failure_is_reported_not_raised(analysis, tmp_path, monkeypatch):
    def explode(*_args, **_kwargs):
        raise ConnectionError("dns failure")

    monkeypatch.setattr("httpx2.post", explode, raising=False)
    monkeypatch.setattr("httpx.post", explode, raising=False)

    sent, message = send_report(analysis, settings=_configured(tmp_path))
    assert sent is False
    assert "ConnectionError" in message


def test_error_detail_never_echoes_the_api_key(analysis, tmp_path, monkeypatch):
    """A leaked key in a log or diagnostics list is a real incident."""
    s = _configured(tmp_path)
    leak = {"message": f"bad key {FAKE_RESEND_KEY} supplied"}
    _capture(monkeypatch, _Response(401, leak))
    sent, message = send_report(analysis, settings=s)
    assert sent is False
    assert FAKE_RESEND_KEY not in message, "the key was echoed back verbatim"
    assert "re_***" in message
    assert not any(FAKE_RESEND_KEY in w for w in analysis.diagnostics.warnings)


def test_delivery_outcome_is_recorded_in_diagnostics(analysis, tmp_path, monkeypatch):
    s = _configured(tmp_path)
    _capture(monkeypatch)
    send_report(analysis, settings=s)
    assert any("Report emailed to Info@tencapital.group" in n for n in analysis.diagnostics.info)


def test_delivery_note_reaches_the_json_record(analysis, tmp_path, monkeypatch):
    """The audit trail must show that the report left the building."""
    from reporting.record import build_record

    s = _configured(tmp_path)
    _capture(monkeypatch)
    send_report(analysis, settings=s)
    record = build_record(analysis)
    assert any("Report emailed" in n for n in record["run"]["notes"])


# --------------------------------------------------------------------------
# Pipeline integration
# --------------------------------------------------------------------------


def test_pipeline_deliver_is_a_noop_when_unconfigured(analysis, tmp_path):
    from pipeline import deliver

    s = Settings()
    s.output_dir = tmp_path
    s.resend_api_key = None
    sent, message = deliver(analysis, settings=s)
    assert sent is False
    assert "not configured" in message


def test_run_analysis_can_suppress_email(sample_decks, tmp_path, monkeypatch):
    """`--no-email` / an unticked box must actually prevent the send."""
    from pipeline import run_analysis

    s = _configured(tmp_path)
    captured = _capture(monkeypatch)
    run_analysis(
        sample_decks["pptx"], settings=s, output_dir=tmp_path, email=False
    )
    assert "json" not in captured, "an email was sent despite email=False"


def test_run_analysis_delivers_by_default(sample_decks, tmp_path, monkeypatch):
    from pipeline import run_analysis

    s = _configured(tmp_path)
    captured = _capture(monkeypatch)
    run_analysis(sample_decks["pptx"], settings=s, output_dir=tmp_path)
    assert captured.get("json"), "no email was sent on a default run"
    assert captured["json"]["to"] == ["Info@tencapital.group"]
