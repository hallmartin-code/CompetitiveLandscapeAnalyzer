"""
The single point at which this application talks to a language model.

ARCHITECTURAL DECISION -- one choke point, structured output only
-----------------------------------------------------------------
Every LLM call in the system goes through `LLMClient.structured()`, which
requires a Pydantic output schema. There is no free-text call path and no
regex-parsing of prose anywhere in the codebase. Consequences:

* Malformed model output becomes a validation error at a single, testable
  boundary instead of a subtly wrong field deep in a report.
* The grounding rules (the "analyst charter" below) are attached to every call
  by construction, so no future module can accidentally ask for an ungrounded
  answer.
* Cost, retries, timeouts and degradation are handled once.

The charter is sent as a cached system prefix. It is byte-stable across calls,
which is what makes the cache hit -- per-run content goes in the user turn.

Degradation: if no credential is configured, or the API fails, `structured()`
returns `(None, reason)`. Callers fall back to the deterministic heuristics in
their own module and record the degradation in `RunDiagnostics`. The app is
never left with a blank report and never presents a degraded run as a full one.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple, Type, TypeVar

from pydantic import BaseModel, ValidationError

from config import Settings, settings as default_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


#: Shared grounding rules. Stable bytes -> cacheable prefix.
ANALYST_CHARTER = """\
You are a competitive intelligence analyst supporting venture capital investment \
diligence at TEN Capital. You produce evidence-grounded analysis for investors who \
will make capital allocation decisions from it.

NON-NEGOTIABLE RULES

1. Evidence over assertion. Distinguish rigorously between:
   - stated_fact: a verifiable, attributable statement
   - company_claim: asserted by the company and not independently corroborated
   - analyst_inference: a conclusion you derived; it must be labelled as such
   - unverified: present in the material but with no usable provenance
   Anything a pitch deck says about its own product, traction or superiority is a
   company_claim unless the deck cites an external, checkable source.

2. Never fabricate. You must NOT invent competitor revenue, funding amounts,
   pricing, customer counts, valuations, market share or partnerships. If you do
   not know a value from the supplied material or from well-established public
   knowledge, output exactly "Not publicly disclosed". If a qualitative attribute
   is unknown, output exactly "Unknown". A missing value is a legitimate and
   useful analytical output; a plausible invented one is a serious defect.

3. Cite locators. When a claim comes from the supplied material, reference the
   slide or page it came from (e.g. "Slide 5", "Page 3"). If it comes from your
   own general knowledge of the market, mark it as such and set lower confidence.

4. Confidence must be calibrated. Use the full 0.0-1.0 range. Reserve values
   above 0.85 for facts stated explicitly in the source material. Anything you
   reasoned your way to belongs below 0.6.

5. Think like an investor, not a marketer. Competition is for the same customer,
   the same workflow and the same budget -- not merely the same sector. Manual
   processes, internal builds, outsourcing and doing nothing are real
   competitors. Distinguish a feature difference from a durable, defensible
   advantage. Incumbent distribution is itself a competitive advantage.

6. Be concise. Output is destined for a one-page investor report. Prefer short,
   specific, decision-relevant phrasing over completeness.
"""


class LLMUnavailable(Exception):
    """Raised only by callers that have no meaningful fallback."""


def _blocked_domains(error_text: str) -> list:
    """Extract domains the search tool refused from its 400 message."""
    import re

    if "not accessible to our user agent" not in error_text:
        return []
    match = re.search(r"\[([^\]]*)\]", error_text)
    if not match:
        return []
    return [d.strip().strip("'\"") for d in match.group(1).split(",") if d.strip()]


def _collect_search_urls(message) -> list:
    """Pull the URLs the server-side search tool actually consulted.

    Recorded so the report can show which public sources were touched, and so
    a run with research enabled but zero results is visibly distinguishable
    from one that never searched. Server tools return errors as *content*
    (HTTP 200), so success is an list and failure an object -- branch on that.
    """
    urls: list = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        content = getattr(block, "content", None)
        if not isinstance(content, list):
            continue  # an error object, not results
        for item in content:
            url = getattr(item, "url", None)
            title = getattr(item, "title", None)
            if url and url not in [u["url"] for u in urls]:
                urls.append({"url": url, "title": title or ""})
    return urls


class LLMClient:
    """Thin, opinionated wrapper around the Anthropic Messages API."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or default_settings
        self._client = None
        self._init_error: Optional[str] = None
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        #: URLs consulted by the most recent web-search-enabled call.
        self.last_search_urls: list = []
        #: Allowlisted domains the search tool refused (crawler-blocked).
        self.blocked_domains: list = []

        if not self.settings.llm_available:
            self._init_error = (
                "No Anthropic credential found. Set ANTHROPIC_API_KEY (see .env.example) "
                "to enable model-backed analysis; the app will otherwise run in "
                "reduced-confidence heuristic mode."
            )
            return

        try:
            import anthropic

            # Retries and timeout are set on the client so every call inherits
            # them. The SDK retries 408/409/429/5xx with exponential backoff.
            self._client = anthropic.Anthropic(
                timeout=self.settings.request_timeout,
                max_retries=self.settings.max_retries,
                **({"api_key": self.settings.api_key} if self.settings.api_key else {}),
            )
        except ImportError:
            self._init_error = "The 'anthropic' package is not installed; run pip install -r requirements.txt"
        except Exception as exc:  # pragma: no cover - construction rarely fails
            self._init_error = f"Could not initialise the Anthropic client: {exc}"

    # -- capability -------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def unavailable_reason(self) -> Optional[str]:
        return self._init_error

    # -- the only call path -----------------------------------------------

    def structured(
        self,
        user_prompt: str,
        output_model: Type[T],
        *,
        label: str = "llm-call",
        extra_system: str = "",
        max_tokens: Optional[int] = None,
        web_search: bool = False,
        allowed_domains: Optional[list] = None,
        max_searches: int = 6,
    ) -> Tuple[Optional[T], Optional[str]]:
        """Run one grounded, schema-validated model call.

        Set `web_search=True` to attach Anthropic's server-side web search tool,
        restricted to `allowed_domains`. Callers must only do so once consent
        and the operator allowlist have both been checked -- this method does
        not decide policy, `research/source_manager.py` does.

        Returns `(parsed, None)` on success or `(None, reason)` on any failure.
        Never raises: an analysis run degrades, it does not crash.
        """
        if not self.available:
            return None, self._init_error or "Model unavailable."

        import anthropic

        # Stable prefix first (cacheable), volatile per-call guidance after.
        system: list[dict] = [
            {
                "type": "text",
                "text": ANALYST_CHARTER,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if extra_system:
            system.append({"type": "text", "text": extra_system})

        # Server-side web search. `_20260209` is the current variant and is
        # supported on Opus 5; it runs its own filtering internally, so we do
        # not additionally declare a code execution tool.
        tools: list[dict] = []
        if web_search:
            tool: dict = {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": max_searches,
            }
            if allowed_domains:
                tool["allowed_domains"] = list(allowed_domains)[:64]
            tools.append(tool)

        try:
            # Streaming: these are long, high-effort analytical turns and a
            # non-streaming request risks an HTTP timeout at this max_tokens.
            with self._client.messages.stream(
                model=self.settings.model,
                max_tokens=max_tokens or self.settings.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
                # Adaptive thinking: the model decides how much to reason.
                thinking={"type": "adaptive"},
                output_config={"effort": self.settings.effort},
                output_format=output_model,
                **({"tools": tools} if tools else {}),
            ) as stream:
                message = stream.get_final_message()

        except anthropic.APITimeoutError:
            return None, f"[{label}] The model request timed out after {self.settings.request_timeout:.0f}s."
        except anthropic.RateLimitError:
            return None, f"[{label}] Rate limited by the Anthropic API after {self.settings.max_retries} retries."
        except anthropic.AuthenticationError:
            return None, f"[{label}] Anthropic API authentication failed. Check ANTHROPIC_API_KEY."
        except anthropic.BadRequestError as exc:
            # Some publishers block Anthropic's crawler, and the search tool
            # rejects the whole request rather than skipping them. Drop the
            # named domains and retry once instead of losing the analysis.
            blocked = _blocked_domains(str(exc))
            if blocked and allowed_domains:
                remaining = [
                    d for d in allowed_domains
                    if not any(b == d or d.endswith("." + b) for b in blocked)
                ]
                if remaining and len(remaining) < len(allowed_domains):
                    logger.warning(
                        "%s: retrying without crawler-blocked domains: %s", label, blocked
                    )
                    self.blocked_domains = sorted(set(self.blocked_domains) | set(blocked))
                    return self.structured(
                        user_prompt,
                        output_model,
                        label=label,
                        extra_system=extra_system,
                        max_tokens=max_tokens,
                        web_search=web_search,
                        allowed_domains=remaining,
                        max_searches=max_searches,
                    )
            return None, f"[{label}] The model rejected the request: {exc}"
        except anthropic.APIStatusError as exc:
            return None, f"[{label}] Anthropic API error (HTTP {exc.status_code}): {exc}"
        except anthropic.APIConnectionError as exc:
            return None, f"[{label}] Could not reach the Anthropic API: {exc}"
        except Exception as exc:  # defensive: unexpected SDK/runtime failure
            logger.exception("Unexpected LLM failure in %s", label)
            return None, f"[{label}] Unexpected model failure: {exc.__class__.__name__}: {exc}"

        self.calls += 1
        self.last_search_urls = _collect_search_urls(message)
        usage = getattr(message, "usage", None)
        if usage is not None:
            self.input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.output_tokens += getattr(usage, "output_tokens", 0) or 0

        # Safety classifiers can decline a request with HTTP 200.
        if getattr(message, "stop_reason", None) == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) or "unspecified"
            return None, f"[{label}] The model declined this request (category: {category})."

        if getattr(message, "stop_reason", None) == "max_tokens":
            return None, (
                f"[{label}] The model response hit the {max_tokens or self.settings.max_tokens} "
                "token ceiling and was truncated. Raise CI_MAX_TOKENS or reduce the input."
            )

        parsed = getattr(message, "parsed_output", None)
        if parsed is None:
            # Fall back to parsing the text block; output_config.format
            # guarantees valid JSON but the helper attribute may be absent on
            # some SDK paths.
            import json

            text = next(
                (b.text for b in message.content if getattr(b, "type", None) == "text"), None
            )
            if not text:
                return None, f"[{label}] The model returned no parseable content."
            try:
                parsed = output_model.model_validate(json.loads(text))
            except (json.JSONDecodeError, ValidationError) as exc:
                return None, f"[{label}] The model returned output that failed schema validation: {exc}"

        logger.info(
            "%s: ok (in=%s out=%s tokens)", label, self.input_tokens, self.output_tokens
        )
        return parsed, None

    # -- reporting ---------------------------------------------------------

    def usage_summary(self) -> str:
        if not self.calls:
            return "No model calls made."
        return (
            f"{self.calls} model call(s) to {self.settings.model} at effort "
            f"'{self.settings.effort}'; {self.input_tokens:,} input / "
            f"{self.output_tokens:,} output tokens."
        )
