"""
Automated report delivery by email, via Resend.

ARCHITECTURAL DECISION -- the email is a briefing, not a covering note
----------------------------------------------------------------------
The body carries the decision-relevant findings in full: score, band,
confidence, the recommended positioning, the competitive set with threat
levels, and the investor watchlist. A recipient reading it on a phone gets
the same judgement the PDF gives, and opens the attachment only when they
want the layout and the market map.

Two rules the rest of the system also follows apply here:

* **It never breaks a run.** A failed send is a warning in `RunDiagnostics`,
  never an exception, and never blocks the PDF the analyst is waiting for. The
  report is the deliverable; the email is a convenience.
* **It never fabricates.** Sentinels are rendered as they are, and a degraded
  run is flagged in the subject line so a thin report cannot be mistaken for a
  complete one in an inbox.

Sent with a plain HTTPS POST rather than the Resend SDK: it is a single
endpoint, and the project already depends on an HTTP client through the
Anthropic SDK. One less dependency to keep current.
"""

from __future__ import annotations

import base64
import logging
import re
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import Settings, settings as default_settings
from models.schemas import (
    CATEGORY_LABEL,
    NOT_DISCLOSED,
    UNKNOWN,
    CompetitiveAnalysis,
    RunDiagnostics,
)

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"

#: Resend caps the whole request; stay well clear.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

_SEVERITY_COLOR = {"high": "#B91C1C", "medium": "#B45309", "low": "#15803D"}
_THREAT_COLOR = dict(_SEVERITY_COLOR)
_ACCENT = "#1E3A5F"
_INK = "#111827"
_MUTED = "#6B7280"
_RULE = "#E5E7EB"


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------


def _clean(text: Optional[str], fallback: str = "—") -> str:
    text = " ".join(str(text or "").split())
    if not text or text in {UNKNOWN, NOT_DISCLOSED}:
        return fallback if text != NOT_DISCLOSED else "Not publicly disclosed"
    return text


def subject_line(analysis: CompetitiveAnalysis) -> str:
    """Scannable in an inbox list: company, verdict, and any health warning."""
    name = analysis.profile.display_name()
    score = analysis.score
    parts = [f"Competitive Intelligence — {name}", f"{score.total}/100 {score.band}"]
    if analysis.diagnostics.degraded:
        parts.append("DEGRADED RUN")
    return " · ".join(parts)


def _score_colour(total: int) -> str:
    if total >= 65:
        return "#15803D"
    if total >= 50:
        return "#B45309"
    return "#B91C1C"


def build_html(analysis: CompetitiveAnalysis, settings: Settings) -> str:
    """Inline-styled HTML: email clients strip <style> blocks."""
    p = analysis.profile
    s = analysis.score
    d = analysis.diagnostics

    meta = " | ".join(
        x for x in (
            p.sector.display(""), p.subsector.display(""),
            p.funding_stage.display("") or p.current_round.display(""),
        ) if x
    )

    rows: List[str] = []
    for c in analysis.selected_competitors(settings.matrix_rows):
        colour = _THREAT_COLOR.get(c.threat_level.value, _MUTED)
        rows.append(
            f'<tr>'
            f'<td style="padding:6px 10px 6px 0;border-bottom:1px solid {_RULE};'
            f'font-size:13px;color:{_INK};">{escape(_clean(c.name))}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid {_RULE};'
            f'font-size:12px;color:{_MUTED};">{escape(CATEGORY_LABEL[c.category])}</td>'
            f'<td style="padding:6px 0;border-bottom:1px solid {_RULE};font-size:12px;'
            f'font-weight:600;color:{colour};text-transform:uppercase;">'
            f'{escape(c.threat_level.value)}</td>'
            f'</tr>'
        )
    competitor_table = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        f'style="border-collapse:collapse;margin:6px 0 0;">{"".join(rows)}</table>'
        if rows else f'<p style="color:{_MUTED};font-size:13px;">No competitors identified.</p>'
    )

    concerns: List[str] = []
    for c in analysis.concerns[:5]:
        colour = _SEVERITY_COLOR.get(c.severity, _MUTED)
        concerns.append(
            f'<div style="margin:0 0 12px;padding:0 0 0 10px;border-left:3px solid {colour};">'
            f'<div style="font-size:13px;font-weight:600;color:{_INK};">'
            f'{escape(_clean(c.concern))}</div>'
            f'<div style="font-size:12px;color:{_MUTED};margin-top:3px;">'
            f'<strong>Risk:</strong> {escape(_clean(c.current_risk))}<br>'
            f'<strong>Ask:</strong> {escape(_clean(c.diligence_question))}<br>'
            f'<strong>Needs:</strong> {escape(_clean(c.evidence_needed))}</div>'
            f'</div>'
        )
    concerns_html = "".join(concerns) or (
        f'<p style="color:{_MUTED};font-size:13px;">No concerns recorded.</p>'
    )

    whitespace = "".join(
        f'<li style="font-size:13px;color:{_INK};margin-bottom:5px;">'
        f'{escape(_clean(w.opportunity))} '
        f'<span style="color:{_MUTED};font-size:11px;">({escape(w.difficulty)} difficulty)</span>'
        f'</li>'
        for w in analysis.whitespace[:4]
    )
    whitespace_html = (
        f'<ul style="margin:6px 0 0;padding-left:18px;">{whitespace}</ul>'
        if whitespace else f'<p style="color:{_MUTED};font-size:13px;">None identified.</p>'
    )

    banner = ""
    if d.degraded:
        banner = (
            '<div style="background:#FEF2F2;border:1px solid #FECACA;border-radius:4px;'
            'padding:10px 12px;margin:0 0 18px;font-size:12px;color:#991B1B;">'
            '<strong>Degraded run.</strong> This analysis ran with reduced capability. '
            'Read the warnings in the attached JSON record before relying on it.</div>'
        )

    warnings_html = ""
    if d.warnings:
        items = "".join(
            f'<li style="margin-bottom:3px;">{escape(w)}</li>' for w in d.warnings[:5]
        )
        warnings_html = (
            f'<div style="margin-top:22px;padding-top:14px;border-top:1px solid {_RULE};">'
            f'<div style="font-size:11px;font-weight:700;color:{_MUTED};'
            f'text-transform:uppercase;letter-spacing:.06em;">Run warnings</div>'
            f'<ul style="margin:6px 0 0;padding-left:18px;font-size:11px;color:{_MUTED};">'
            f'{items}</ul></div>'
        )

    evidence_base = (
        "Deck + public sources" if d.research_performed
        else "Deck + market knowledge" if d.llm_available
        else "Deck only — reduced confidence"
    )

    def section(title: str, body: str) -> str:
        return (
            f'<div style="margin-top:24px;">'
            f'<div style="font-size:11px;font-weight:700;color:{_ACCENT};'
            f'text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid {_RULE};'
            f'padding-bottom:5px;">{escape(title)}</div>{body}</div>'
        )

    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#F3F4F6;">
<table role="presentation" cellpadding="0" cellspacing="0" width="100%"
       style="background:#F3F4F6;padding:24px 12px;">
<tr><td align="center">
<table role="presentation" cellpadding="0" cellspacing="0" width="100%"
       style="max-width:640px;background:#FFFFFF;border-radius:6px;padding:28px 30px;
              font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
<tr><td>

  <div style="font-size:11px;font-weight:700;color:{_ACCENT};letter-spacing:.09em;">
    COMPETITIVE INTELLIGENCE</div>
  <div style="font-size:24px;font-weight:700;color:{_INK};margin-top:4px;">
    {escape(p.display_name())}</div>
  <div style="font-size:12px;color:{_MUTED};margin-top:3px;">{escape(meta)}</div>
  <div style="height:2px;background:{_ACCENT};margin:14px 0 18px;"></div>

  {banner}

  <table role="presentation" cellpadding="0" cellspacing="0">
    <tr>
      <td style="padding-right:22px;vertical-align:top;">
        <div style="font-size:40px;font-weight:700;color:{_score_colour(s.total)};
                    line-height:1;">{s.total}<span style="font-size:15px;color:{_MUTED};
                    font-weight:400;">/100</span></div>
        <div style="font-size:13px;font-weight:600;color:{_INK};margin-top:5px;">
          {escape(s.band)}</div>
        <div style="font-size:11px;color:{_MUTED};margin-top:2px;">
          Confidence {s.confidence:.2f}</div>
      </td>
      <td style="vertical-align:top;">
        <div style="font-size:13px;color:#374151;line-height:1.5;">
          {escape(_clean(analysis.assessment or s.explanation))}</div>
      </td>
    </tr>
  </table>

  <div style="background:#E8EDF3;border-left:3px solid {_ACCENT};padding:12px 14px;
              margin-top:18px;">
    <div style="font-size:10px;font-weight:700;color:{_ACCENT};letter-spacing:.07em;">
      RECOMMENDED POSITIONING</div>
    <div style="font-size:13px;font-style:italic;color:{_INK};margin-top:5px;line-height:1.5;">
      {escape(_clean(analysis.positioning.recommended, "Not established."))}</div>
  </div>

  {section("Competitive set", competitor_table)}
  {section("Investor watchlist", f'<div style="margin-top:10px;">{concerns_html}</div>')}
  {section("Whitespace", whitespace_html)}

  {warnings_html}

  <div style="margin-top:26px;padding-top:14px;border-top:1px solid {_RULE};
              font-size:11px;color:{_MUTED};line-height:1.6;">
    Evidence base: {escape(evidence_base)} &middot;
    {len(analysis.competitors)} competitors assessed &middot;
    {d.llm_calls} model call(s)<br>
    Generated {escape(analysis.analysis_timestamp)}<br>
    <strong style="color:{_ACCENT};">TEN Capital Decision Intelligence</strong> &middot;
    <em>Confidential – for recipients only.</em>
  </div>

</td></tr></table>
</td></tr></table>
</body></html>"""


def build_text(analysis: CompetitiveAnalysis, settings: Settings) -> str:
    """Plain-text alternative. Improves deliverability and is not optional."""
    p = analysis.profile
    s = analysis.score
    lines = [
        "COMPETITIVE INTELLIGENCE",
        p.display_name(),
        "=" * 60,
        f"Differentiation: {s.total}/100 — {s.band} (confidence {s.confidence:.2f})",
        "",
    ]
    if analysis.diagnostics.degraded:
        lines += ["** DEGRADED RUN — read the warnings before relying on this. **", ""]

    lines += [_clean(analysis.assessment or s.explanation), ""]
    lines += ["RECOMMENDED POSITIONING", _clean(analysis.positioning.recommended, "Not established."), ""]

    lines.append("COMPETITIVE SET")
    for c in analysis.selected_competitors(settings.matrix_rows):
        lines.append(
            f"  - {_clean(c.name)} [{CATEGORY_LABEL[c.category]}] threat={c.threat_level.value}"
        )
    lines.append("")

    lines.append("INVESTOR WATCHLIST")
    for c in analysis.concerns[:5]:
        lines += [
            f"  [{c.severity.upper()}] {_clean(c.concern)}",
            f"      Risk:  {_clean(c.current_risk)}",
            f"      Ask:   {_clean(c.diligence_question)}",
            f"      Needs: {_clean(c.evidence_needed)}",
        ]
    lines.append("")

    if analysis.whitespace:
        lines.append("WHITESPACE")
        for w in analysis.whitespace[:4]:
            lines.append(f"  - ({w.difficulty}) {_clean(w.opportunity)}")
        lines.append("")

    if analysis.diagnostics.warnings:
        lines.append("RUN WARNINGS")
        lines += [f"  - {w}" for w in analysis.diagnostics.warnings[:5]]
        lines.append("")

    lines += [
        "-" * 60,
        f"{len(analysis.competitors)} competitors assessed · "
        f"{analysis.diagnostics.llm_calls} model call(s)",
        f"Generated {analysis.analysis_timestamp}",
        "TEN Capital Decision Intelligence — Confidential, for recipients only.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------


def _attachment(path: Path) -> Optional[Dict[str, str]]:
    """Base64-encode a file for the Resend payload, or skip it with a reason."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > MAX_ATTACHMENT_BYTES:
        return None
    return {"filename": path.name, "content": base64.b64encode(data).decode("ascii")}


def build_payload(
    analysis: CompetitiveAnalysis,
    pdf_path: Optional[Path],
    json_path: Optional[Path],
    settings: Settings,
    subject_prefix: str = "",
) -> Dict[str, Any]:
    """Assemble the Resend request body. Pure: no I/O beyond reading attachments."""
    payload: Dict[str, Any] = {
        "from": settings.email_from,
        "to": list(settings.email_to),
        "subject": f"{subject_prefix}{subject_line(analysis)}",
        "html": build_html(analysis, settings),
        "text": build_text(analysis, settings),
    }
    if settings.email_reply_to:
        payload["reply_to"] = settings.email_reply_to

    attachments: List[Dict[str, str]] = []
    if settings.email_attach_pdf and pdf_path:
        item = _attachment(Path(pdf_path))
        if item:
            attachments.append(item)
    if settings.email_attach_json and json_path:
        item = _attachment(Path(json_path))
        if item:
            attachments.append(item)
    if attachments:
        payload["attachments"] = attachments
    return payload


def send_report(
    analysis: CompetitiveAnalysis,
    pdf_path: Optional[Path] = None,
    json_path: Optional[Path] = None,
    settings: Optional[Settings] = None,
    diagnostics: Optional[RunDiagnostics] = None,
    subject_prefix: str = "",
) -> Tuple[bool, str]:
    """Email the completed analysis. Returns `(sent, message)`; never raises."""
    settings = settings or default_settings
    diagnostics = diagnostics or analysis.diagnostics

    if not settings.email_enabled:
        return False, "Email delivery is disabled (EMAIL_ENABLED=false)."
    if not settings.resend_api_key:
        return False, "Email not sent: RESEND_API_KEY is not configured."
    if not settings.email_to:
        return False, "Email not sent: no recipient configured (EMAIL_TO)."

    try:
        try:
            import httpx2 as httpx
        except ImportError:  # pragma: no cover
            import httpx  # type: ignore

        payload = build_payload(analysis, pdf_path, json_path, settings, subject_prefix)
        response = httpx.post(
            RESEND_ENDPOINT,
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            timeout=45.0,
        )

        if response.status_code >= 400:
            detail = _error_detail(response)
            message = f"Email delivery failed (HTTP {response.status_code}): {detail}"
            diagnostics.warn(message)
            logger.warning(message)
            return False, message

        message_id = ""
        try:
            message_id = (response.json() or {}).get("id", "") or ""
        except Exception:
            pass

        recipients = ", ".join(settings.email_to)
        attached = len(payload.get("attachments", []))
        message = (
            f"Report emailed to {recipients}"
            + (f" ({attached} attachment(s))" if attached else "")
            + (f" [id {message_id[:12]}]" if message_id else "")
            + "."
        )
        diagnostics.note(message)
        logger.info(message)
        return True, message

    except Exception as exc:
        # A delivery failure must never cost the analyst the report itself.
        message = f"Email delivery failed: {exc.__class__.__name__}: {exc}"
        diagnostics.warn(message)
        logger.warning(message, exc_info=True)
        return False, message


def _error_detail(response) -> str:
    """Extract a useful message from a Resend error, without echoing the key."""
    try:
        body = response.json() or {}
        detail = body.get("message") or body.get("error") or str(body)
    except Exception:
        detail = (getattr(response, "text", "") or "")[:200]
    detail = str(detail)[:300]
    # Defensive: never let a credential reach a log or a diagnostics list.
    return re.sub(r"re_[A-Za-z0-9_\-]{8,}", "re_***", detail)
