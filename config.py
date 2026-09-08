"""
Central configuration.

ARCHITECTURAL DECISION -- one settings object, no module-level env reads
-----------------------------------------------------------------------
Every tunable (model id, effort, page budget, research allowlist) lives here so
that a reviewer can audit the system's behaviour from a single file, and so
tests can construct a `Settings` with different values without touching the
process environment.

Nothing here performs I/O against the network. `Settings.research_enabled`
is an *authorisation* flag; the research layer additionally requires explicit
per-run user consent (`Consent.public_research_permitted`). Both must be true.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

try:  # optional; the app works without a .env file
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is a convenience, not a requirement
    pass


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "outputs"
SAMPLE_DIR = PROJECT_ROOT / "samples"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_list(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


#: Domains the research layer may fetch from when research is authorised.
#: Deliberately conservative: an allowlist, not a blocklist. Anything not on
#: this list is refused and recorded as a diagnostic rather than fetched.
#:
#: Note: several major publishers (Reuters, WSJ, FT) block Anthropic's crawler
#: and are rejected outright by the search tool, so they are omitted here. The
#: client removes any such domain and retries rather than failing the run, so
#: an operator adding one back degrades gracefully.
DEFAULT_SOURCE_ALLOWLIST = [
    "sec.gov",
    "clinicaltrials.gov",
    "fda.gov",
    "uspto.gov",
    "europa.eu",
    "nih.gov",
    "who.int",
    "crunchbase.com",
    "pitchbook.com",
    "techcrunch.com",
    "businesswire.com",
    "prnewswire.com",
    "statista.com",
    "gartner.com",
    "himss.org",
    "cms.gov",
]


@dataclass
class Settings:
    """Runtime settings for one process."""

    # --- Model configuration -------------------------------------------------
    # Default to the most capable model: this is diligence output that an
    # investor may act on, so analytical quality outranks per-run cost.
    model: str = field(default_factory=lambda: os.getenv("CI_MODEL", "claude-opus-5"))
    #: Effort trades thoroughness against tokens. `high` is the sweet spot for
    #: this workload; raise to `xhigh` for unusually complex decks.
    effort: str = field(default_factory=lambda: os.getenv("CI_EFFORT", "high"))
    max_tokens: int = field(default_factory=lambda: _env_int("CI_MAX_TOKENS", 32000))
    #: Seconds. Analysis calls think for a while; the SDK default is 10 min.
    request_timeout: float = field(
        default_factory=lambda: float(os.getenv("CI_TIMEOUT", "600"))
    )
    max_retries: int = field(default_factory=lambda: _env_int("CI_MAX_RETRIES", 3))
    api_key: Optional[str] = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))

    # --- Pipeline behaviour --------------------------------------------------
    #: Hard ceiling on deck transcript sent to the model. Decks are small; this
    #: exists to catch pathological inputs, and a hit is always reported.
    max_transcript_chars: int = field(
        default_factory=lambda: _env_int("CI_MAX_TRANSCRIPT_CHARS", 120_000)
    )
    min_competitors: int = field(default_factory=lambda: _env_int("CI_MIN_COMPETITORS", 4))
    max_competitors: int = field(default_factory=lambda: _env_int("CI_MAX_COMPETITORS", 12))
    #: Competitors rendered in the one-page matrix (spec: 4-7).
    matrix_rows: int = field(default_factory=lambda: _env_int("CI_MATRIX_ROWS", 6))
    matrix_rows_min: int = 4
    whitespace_count: int = field(default_factory=lambda: _env_int("CI_WHITESPACE", 4))
    concern_count: int = field(default_factory=lambda: _env_int("CI_CONCERNS", 5))

    # --- Research ------------------------------------------------------------
    #: Operator-level authorisation. Per-run user consent is still required.
    research_enabled: bool = field(default_factory=lambda: _env_bool("CI_RESEARCH_ENABLED", True))
    source_allowlist: List[str] = field(
        default_factory=lambda: _env_list("CI_SOURCE_ALLOWLIST", DEFAULT_SOURCE_ALLOWLIST)
    )
    #: Optional TEN Capital CRM endpoint. Transmission additionally requires
    #: `Consent.external_transmission_permitted`; unset means never transmit.
    crm_endpoint: Optional[str] = field(default_factory=lambda: os.getenv("TEN_CRM_ENDPOINT") or None)
    crm_api_key: Optional[str] = field(default_factory=lambda: os.getenv("TEN_CRM_API_KEY") or None)

    # --- Email notification (Resend) ----------------------------------------
    #: Operator-configured automation: when a key and a recipient are present,
    #: every completed analysis is emailed. This is deliberately NOT gated on
    #: per-run consent the way `crm_endpoint` is -- the recipient is the firm's
    #: own inbox, configured once at deploy time, so delivery is closer to
    #: filing the report than to disclosing it. An analyst can still suppress
    #: any single send from the UI, and every send is recorded in the JSON
    #: record and the run diagnostics.
    resend_api_key: Optional[str] = field(default_factory=lambda: os.getenv("RESEND_API_KEY") or None)
    email_enabled: bool = field(default_factory=lambda: _env_bool("EMAIL_ENABLED", True))
    email_to: List[str] = field(
        default_factory=lambda: _env_list("EMAIL_TO", ["Info@tencapital.group"])
    )
    #: Must be on a domain verified in Resend, or the API rejects the send.
    email_from: str = field(
        default_factory=lambda: os.getenv(
            "EMAIL_FROM", "TEN Capital Decision Intelligence <reports@tencapital.group>"
        )
    )
    email_reply_to: Optional[str] = field(default_factory=lambda: os.getenv("EMAIL_REPLY_TO") or None)
    email_attach_pdf: bool = field(default_factory=lambda: _env_bool("EMAIL_ATTACH_PDF", True))
    email_attach_json: bool = field(default_factory=lambda: _env_bool("EMAIL_ATTACH_JSON", True))

    # --- Reporting -----------------------------------------------------------
    output_dir: Path = field(default_factory=lambda: Path(os.getenv("CI_OUTPUT_DIR", str(OUTPUT_DIR))))
    #: The report is a one-pager. This is enforced, not requested -- see
    #: `reporting/pdf_generator.py` for the reduction ladder.
    max_pages: int = 1
    #: How many progressively-more-aggressive layout passes to attempt before
    #: giving up and reporting the failure.
    max_layout_passes: int = 6

    # --- Logging -------------------------------------------------------------
    log_level: str = field(default_factory=lambda: os.getenv("CI_LOG_LEVEL", "INFO"))

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @property
    def email_configured(self) -> bool:
        """True when an email could actually be sent."""
        return bool(self.email_enabled and self.resend_api_key and self.email_to)

    @property
    def llm_available(self) -> bool:
        """True when a credential is present.

        Note the SDK also resolves `ANTHROPIC_AUTH_TOKEN` and `ant auth login`
        profiles, so we treat those as available too.
        """
        return bool(
            self.api_key
            or os.getenv("ANTHROPIC_AUTH_TOKEN")
            or os.getenv("ANTHROPIC_PROFILE")
            or (Path.home() / ".config" / "anthropic").exists()
        )

    def domain_allowed(self, url: str) -> bool:
        """Allowlist check used before any outbound fetch."""
        url = (url or "").lower()
        if not url.startswith(("http://", "https://")):
            return False
        host = url.split("://", 1)[1].split("/", 1)[0].split("@")[-1].split(":")[0]
        return any(host == d or host.endswith("." + d) for d in self.source_allowlist)


#: Module-level default; callers may construct their own for tests.
settings = Settings()

# --- Report chrome (TEN Capital) --------------------------------------------
REPORT_TITLE = "Competitive Intelligence"
FOOTER_LEFT = "TEN Capital Decision Intelligence"
FOOTER_RIGHT = "Confidential – for recipients only."

# --- Differentiation score weighting (spec section 7) ------------------------
SCORE_WEIGHTS: List[tuple] = [
    ("Product / Technology", 20),
    ("Customer Value Proposition", 15),
    ("Competitive Performance", 15),
    ("Distribution / GTM", 10),
    ("Proprietary Data / IP", 10),
    ("Switching Costs / Network Effects", 10),
    ("Market Positioning", 10),
    ("Sustainability of Advantage", 10),
]

SCORE_BANDS = [
    (80, "Highly differentiated"),
    (65, "Meaningfully differentiated"),
    (50, "Moderate differentiation"),
    (35, "Weak differentiation"),
    (0, "Commodity / highly vulnerable"),
]


def score_band(total: int) -> str:
    for threshold, label in SCORE_BANDS:
        if total >= threshold:
            return label
    return SCORE_BANDS[-1][1]


#: The 18 assessment dimensions from spec section 6, grouped by score category.
#: The grouping is what connects the qualitative assessment to the numeric
#: score -- each category's raw score is derived from its member dimensions.
DIMENSION_GROUPS: dict = {
    "Product / Technology": [
        "Product differentiation",
        "Technology differentiation",
        "Scalability",
    ],
    "Customer Value Proposition": [
        "Customer differentiation",
        "Price advantage",
        "Ease of adoption",
        "Workflow integration",
    ],
    "Competitive Performance": [
        "Performance advantage",
        "Speed to market",
        "Capital efficiency",
    ],
    "Distribution / GTM": [
        "Distribution advantage",
        "Customer relationships",
    ],
    "Proprietary Data / IP": [
        "Proprietary data",
        "Intellectual property",
    ],
    "Switching Costs / Network Effects": [
        "Switching costs",
        "Network effects",
    ],
    "Market Positioning": [
        "Regulatory position",
        "Brand advantage",
    ],
    #: Derived from the `sustainability` axis of the dimensions on which the
    #: company actually holds an advantage -- an advantage that will not last
    #: is not a differentiator, so it earns its own weighted category.
    "Sustainability of Advantage": [],
}

ALL_DIMENSIONS: List[str] = [d for group in DIMENSION_GROUPS.values() for d in group]
