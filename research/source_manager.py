"""
Research authorisation and source handling.

ARCHITECTURAL DECISION -- policy lives here, not at the call sites
------------------------------------------------------------------
Anything that reaches the network is decided in this module. The rule is a
conjunction of three independent gates, all of which must pass:

  1. operator authorisation   (`Settings.research_enabled`)
  2. per-run user consent     (`Consent.public_research_permitted`)
  3. domain allowlist         (`Settings.domain_allowed`)

Refusals are recorded, not silent, so a report produced without research is
visibly distinguishable from one where research simply found nothing.

Outbound transmission of the finished record (to a CRM) is a separate, stricter
gate: it needs `Consent.external_transmission_permitted` *and* a configured
endpoint. Absent either, nothing leaves the machine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from config import Settings, settings as default_settings
from models.schemas import (
    Consent,
    Evidence,
    RunDiagnostics,
    SourceDocument,
    SourceType,
)

logger = logging.getLogger(__name__)


@dataclass
class ResearchPolicy:
    """The decision, plus the reason for it -- both are reportable."""

    permitted: bool
    reason: str
    allowed_domains: List[str] = field(default_factory=list)


def evaluate_policy(
    consent: Consent,
    settings: Optional[Settings] = None,
) -> ResearchPolicy:
    """Decide whether public research may run for this analysis."""
    settings = settings or default_settings

    if not settings.research_enabled:
        return ResearchPolicy(
            False,
            "Public research is disabled by operator configuration (CI_RESEARCH_ENABLED=false).",
        )
    if not consent.public_research_permitted:
        return ResearchPolicy(
            False,
            "Public research was not authorised for this run; analysis uses the supplied "
            "material and the model's existing market knowledge only.",
        )
    if consent.status != "granted":
        return ResearchPolicy(
            False,
            f"Consent status is '{consent.status}'; public research requires explicit consent.",
        )
    if not settings.source_allowlist:
        return ResearchPolicy(False, "No approved source domains are configured.")

    return ResearchPolicy(
        True,
        f"Public research authorised across {len(settings.source_allowlist)} approved domains.",
        list(settings.source_allowlist),
    )


class SourceManager:
    """Gate-keeps and records every external source touched during a run."""

    def __init__(
        self,
        consent: Consent,
        diagnostics: RunDiagnostics,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or default_settings
        self.consent = consent
        self.diagnostics = diagnostics
        self.policy = evaluate_policy(consent, self.settings)
        self.consulted: List[Evidence] = []
        self.refused: List[str] = []
        self.diagnostics.note(self.policy.reason)

    # -- authorisation -----------------------------------------------------

    @property
    def research_permitted(self) -> bool:
        return self.policy.permitted

    @property
    def allowed_domains(self) -> List[str]:
        return self.policy.allowed_domains

    def check_url(self, url: str) -> bool:
        """Allowlist check for a single URL, recording refusals."""
        if not self.research_permitted:
            self.refused.append(f"{url} (research not authorised)")
            return False
        if not self.settings.domain_allowed(url):
            self.refused.append(f"{url} (domain not on the approved list)")
            self.diagnostics.warn(
                f"Skipped '{url}': its domain is not on the approved source list. "
                "Add it to CI_SOURCE_ALLOWLIST if it should be permitted."
            )
            return False
        return True

    # -- user-supplied URLs ------------------------------------------------

    def fetch_urls(self, urls: List[str]) -> List[SourceDocument]:
        """Fetch user-supplied URLs that pass the gates.

        Deliberately a plain HTTP GET with crude text extraction: this path
        exists to admit a specific page the analyst already trusts, not to
        crawl. Anything that fails is skipped with a warning.
        """
        documents: List[SourceDocument] = []
        if not urls:
            return documents

        allowed = [u for u in urls if self.check_url(u)]
        if not allowed:
            return documents

        try:
            import httpx2 as httpx
        except ImportError:  # pragma: no cover
            try:
                import httpx  # type: ignore
            except ImportError:
                self.diagnostics.warn("No HTTP client available; user-supplied URLs were not fetched.")
                return documents

        for url in allowed:
            try:
                response = httpx.get(url, timeout=20.0, follow_redirects=True)
                response.raise_for_status()
                text = _strip_html(response.text)
                if not text.strip():
                    self.diagnostics.warn(f"'{url}' returned no readable text and was skipped.")
                    continue
                doc = SourceDocument(
                    name=url,
                    path=url,
                    doc_type="url",
                    source_type=SourceType.PUBLIC_SOURCE,
                    unit_count=1,
                )
                from models.schemas import DocumentBlock

                doc.blocks.append(
                    DocumentBlock(kind="body", text=text[:20000], index=1, locator=url)
                )
                documents.append(doc)
                self.record(Evidence(source_type=SourceType.PUBLIC_SOURCE, locator=url, url=url))
            except Exception as exc:
                self.diagnostics.warn(f"Could not fetch '{url}': {exc.__class__.__name__}. Skipped.")
        return documents

    # -- recording ---------------------------------------------------------

    def record(self, evidence: Evidence) -> None:
        if evidence.url and evidence.url in [e.url for e in self.consulted]:
            return
        self.consulted.append(evidence)

    def record_search_urls(self, urls: List[dict]) -> None:
        """Record the URLs a server-side search actually consulted."""
        for item in urls or []:
            self.record(
                Evidence(
                    source_type=SourceType.PUBLIC_SOURCE,
                    locator=(item.get("title") or item.get("url", ""))[:60],
                    url=item.get("url"),
                )
            )
        if urls:
            self.diagnostics.research_performed = True

    def summary(self) -> str:
        if not self.research_permitted:
            return "No public research performed (not authorised)."
        if not self.consulted:
            return "Public research authorised but no external sources were consulted."
        return f"{len(self.consulted)} public source(s) consulted."

    # -- outbound transmission (strictest gate) ----------------------------

    def may_transmit(self) -> tuple[bool, str]:
        """Whether the finished record may be sent to a configured endpoint."""
        if not self.consent.external_transmission_permitted:
            return False, "External transmission not authorised by the user."
        if not self.settings.crm_endpoint:
            return False, "No TEN_CRM_ENDPOINT is configured."
        return True, f"Transmission authorised to {self.settings.crm_endpoint}."


def _strip_html(html: str) -> str:
    """Minimal tag stripper. Good enough for an about/product page."""
    import re
    from html import unescape

    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", unescape(text)).strip()
