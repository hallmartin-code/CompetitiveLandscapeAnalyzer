"""
Streamlit interface.

    streamlit run app.py

The workflow follows the spec's section 18, with one addition that matters:
the analysis pauses after competitor discovery so the analyst can *edit the
competitor set* -- deselect false competitors, keep the ones that matter --
before the differentiation score, market map, whitespace and positioning are
computed from it.

That ordering is deliberate. Every downstream conclusion depends on the
competitive set, so letting a human correct it after the fact would leave the
score inconsistent with the competitors shown. Here, editing the set re-runs
everything that depends on it.
"""

from __future__ import annotations

import hmac
import logging
import os
import tempfile
from pathlib import Path
from typing import List, Optional

import streamlit as st

from config import Settings
from ingestion.normalizer import SUPPORTED_DECK_SUFFIXES, SUPPORTED_DOC_SUFFIXES
from models.schemas import (
    CATEGORY_LABEL,
    CompetitiveAnalysis,
    Consent,
    UserInputs,
)
from ui import assets, theme

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

st.set_page_config(
    page_title="Deck to One-Pager — TEN Capital Network",
    page_icon=assets.page_icon(),
    layout="wide",
)

# The brand layer. `st.html` is required here: `st.markdown` runs content
# through a sanitiser that drops <style> blocks, which silently reduces the app
# to Streamlit's defaults while still rendering the markup.
st.html(theme.stylesheet())

SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "🟢"}


def _card(html: str) -> None:
    st.markdown(f'<div class="tc-card">{html}</div>', unsafe_allow_html=True)


def _accepted() -> tuple[list[str], str]:
    """Accepted deck formats, read from the ingestion layer rather than typed.

    The design mockup advertised `.docx`, which this application cannot read.
    Deriving the list from `SUPPORTED_DECK_SUFFIXES` means the interface can
    never promise a format the pipeline would reject.
    """
    suffixes = sorted(x.lstrip(".") for x in SUPPORTED_DECK_SUFFIXES)
    try:
        limit = int(st.get_option("server.maxUploadSize"))
    except Exception:
        limit = 200
    pretty = " &middot; ".join(f"<b>.{x}</b>" for x in suffixes)
    return suffixes, f"{pretty} &nbsp;&middot;&nbsp; up to {limit}&nbsp;MB"


# --------------------------------------------------------------------------
# Access gate
# --------------------------------------------------------------------------


def _require_access() -> None:
    """Password-gate the app when APP_PASSWORD is configured.

    A deployed instance holds a live Anthropic key, and a full analysis costs
    real money per run, so a public URL is a spending liability rather than
    merely an information one. The gate is opt-in: unset APP_PASSWORD and the
    app behaves exactly as it does locally.
    """
    expected = os.getenv("APP_PASSWORD")
    if not expected:
        # Deployed with no gate: say so once, visibly, rather than silently.
        if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("PORT"):
            st.warning(
                "This instance is publicly reachable and has no `APP_PASSWORD` set. "
                "Anyone with the URL can run analyses against your Anthropic key.",
                icon="🔓",
            )
        return

    if st.session_state.get("_authenticated"):
        return

    st.markdown(theme.brand_lockup(), unsafe_allow_html=True)
    st.markdown(
        '<div class="tc-card">'
        + theme.hero(
            "Restricted",
            'Deck Analyzer<span class="arrow">&rarr;</span>'
            '<span class="to">Sign in</span>',
            "This instance is password protected.",
        )
        + "</div>",
        unsafe_allow_html=True,
    )
    with st.form("login"):
        entered = st.text_input("Password", type="password")
        if st.form_submit_button("Enter", type="primary"):
            # Constant-time comparison; a plain == leaks length and prefix.
            if hmac.compare_digest(entered or "", expected):
                st.session_state["_authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.stop()


_require_access()


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------


def _state(key: str, default=None):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


_state("stage", "input")          # input -> review -> report
_state("analysis", None)
_state("deck_path", None)
_state("support_paths", [])
_state("excluded", set())
_state("outputs", None)


def _persist(uploaded, suffix_ok: set) -> Optional[Path]:
    """Save an upload to a temp file; Streamlit gives us bytes, readers want paths."""
    if uploaded is None:
        return None
    suffix = Path(uploaded.name).suffix.lower()
    if suffix not in suffix_ok:
        st.error(f"Unsupported file type '{suffix}'. Supported: {', '.join(sorted(suffix_ok))}.")
        return None
    tmp = Path(tempfile.gettempdir()) / "ci_uploads"
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / uploaded.name
    path.write_bytes(uploaded.getbuffer())
    return path


# --------------------------------------------------------------------------
# Sidebar: configuration and run diagnostics
# --------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Configuration")
    settings = Settings()

    if not settings.llm_available:
        st.warning(
            "No `ANTHROPIC_API_KEY` found. The app will run in heuristic mode: "
            "no competitor discovery, no competitive assessment, and low-confidence "
            "output. See `.env.example`.",
            icon="⚠️",
        )
    else:
        st.caption(f"Model: `{settings.model}`")

    effort = st.select_slider(
        "Reasoning effort",
        options=["low", "medium", "high", "xhigh", "max"],
        value=settings.effort,
        help="Higher effort spends more tokens on analysis. 'high' suits most decks.",
    )
    settings.effort = effort

    settings.matrix_rows = st.slider(
        "Competitors in the matrix", 4, 7, settings.matrix_rows,
        help="The spec's one-page matrix holds 4-7. More may be trimmed to fit.",
    )
    settings.max_competitors = st.slider(
        "Competitors to discover", 4, 16, settings.max_competitors
    )

    st.divider()
    st.markdown("##### Email delivery")
    if settings.email_configured:
        st.caption("Reports are emailed to " + ", ".join(settings.email_to))
    else:
        st.caption("Not configured — set `RESEND_API_KEY` and `EMAIL_TO`.")

    st.divider()
    if st.button("Start over", use_container_width=True):
        for key in ("stage", "analysis", "deck_path", "support_paths", "excluded", "outputs"):
            st.session_state.pop(key, None)
        st.rerun()


# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------

st.markdown(theme.brand_lockup(), unsafe_allow_html=True)
st.markdown(theme.steps(st.session_state.stage), unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Stage 1 — input
# --------------------------------------------------------------------------

if st.session_state.stage == "input":
    deck_types, deck_hint = _accepted()

    _card(
        theme.hero(
            "Competitive Landscape Analyzer",
            'Pitch Deck<span class="arrow">&rarr;</span>'
            '<span class="to">Investor One&#8209;Pager</span>',
            "Upload a pitch deck and get a polished single-page investor PDF, "
            "analyzed and structured by Claude.",
        )
        + f'<div class="tc-lede" style="font-family:{theme.FONT_MONO};font-size:11.5px;'
        f'color:{theme.BRAND["ink_500"]};margin-top:14px;">{deck_hint}</div>'
        + theme.disclosure(settings.email_to, settings.email_configured)
    )

    left, right = st.columns([1, 1.1], gap="large")

    with left:
        st.markdown("#### 1 · Pitch deck")
        deck = st.file_uploader(
            "Click to choose a deck", type=deck_types, key="deck_upload",
            help="Required. Image-only decks will be flagged as low confidence.",
        )

        st.markdown("#### 2 · Supporting material")
        supports = st.file_uploader(
            "Research documents (optional)",
            type=sorted(x.lstrip(".") for x in SUPPORTED_DOC_SUFFIXES),
            accept_multiple_files=True,
        )
        urls_raw = st.text_area(
            "Approved source URLs (optional, one per line)",
            height=70,
            help="Only domains on the approved allowlist are fetched; others are refused and logged.",
        )

        st.markdown("#### 3 · Research and consent")
        allow_research = st.checkbox(
            "Permit public research",
            value=True,
            help="Allows web search across the approved domain allowlist. "
            "Without it, competitor facts come only from the deck and the model's "
            "existing market knowledge.",
        )
        send_email = st.checkbox(
            "Email the report when it is ready"
            + (f" (to {', '.join(settings.email_to)})" if settings.email_configured else ""),
            value=settings.email_configured,
            disabled=not settings.email_configured,
            help="Sends the one-pager and the JSON record to the configured recipient. "
            "Untick to keep a particularly sensitive deck off email for this run.",
        )
        allow_transmit = st.checkbox(
            "Permit transmission to the TEN Capital CRM",
            value=False,
            help="Requires TEN_CRM_ENDPOINT to be configured. Off by default; "
            "nothing leaves this machine unless you enable it.",
        )
        consent_given = st.checkbox(
            "I confirm I am authorised to analyse this material", value=False
        )
        ca, cb = st.columns(2)
        analyst_name = ca.text_input("Analyst name (optional)")
        analyst_email = cb.text_input("Analyst email (optional)")

    with right:
        st.markdown("#### 4 · Company details")
        st.caption("All optional — anything left blank is derived from the deck.")
        c1, c2 = st.columns(2)
        with c1:
            company_name = st.text_input("Company name")
            sector = st.text_input("Sector")
            geography = st.text_input("Geography")
            funding_stage = st.text_input("Funding stage")
            pricing = st.text_input("Pricing")
        with c2:
            website = st.text_input("Website")
            subsector = st.text_input("Subsector")
            business_model = st.text_input("Business model")
            raise_amount = st.text_input("Amount raising")
            traction = st.text_input("Revenue / traction")

        product = st.text_area("Product description", height=68)
        customers = st.text_input("Target customers")
        known_competitors = st.text_input("Known competitors (comma separated)")
        notes = st.text_area("Analyst notes", height=68)

    st.divider()
    ready = deck is not None and consent_given
    if not consent_given:
        st.info("Confirm authorisation above to enable report generation.")

    if st.button(
        "Generate one-pager PDF",
        type="primary",
        disabled=not ready,
        use_container_width=True,
    ):
        deck_path = _persist(deck, {".pdf", ".pptx"})
        if deck_path is None:
            st.stop()

        support_paths: List[Path] = []
        for item in supports or []:
            p = _persist(item, {".pdf", ".pptx", ".txt", ".md"})
            if p:
                support_paths.append(p)

        consent = Consent().grant(public_research=allow_research)
        consent.external_transmission_permitted = allow_transmit

        user = UserInputs(
            user_name=analyst_name or None,
            user_email=analyst_email or None,
            company_name=company_name or None,
            website=website or None,
            sector=sector or None,
            subsector=subsector or None,
            product_description=product or None,
            target_customers=customers or None,
            geography=geography or None,
            business_model=business_model or None,
            pricing=pricing or None,
            known_competitors=known_competitors or None,
            funding_stage=funding_stage or None,
            raise_amount=raise_amount or None,
            traction=traction or None,
            notes=notes or None,
            source_urls=[u.strip() for u in (urls_raw or "").splitlines() if u.strip()],
        )

        from pipeline import analyse

        st.info(
            "This takes roughly 6-10 minutes. Keep this tab open — the analysis "
            "runs live and closing the tab abandons it.",
            icon="⏱️",
        )
        bar = st.progress(0.0, text="Starting…")

        def progress(i: int, n: int, label: str) -> None:
            bar.progress(i / n, text=f"{label}  ·  step {i} of {n}")

        with st.spinner("Running competitive analysis…"):
            analysis = analyse(
                deck_path,
                user=user,
                consent=consent,
                supporting_paths=support_paths,
                settings=settings,
                progress=progress,
            )
        bar.empty()

        st.session_state.analysis = analysis
        st.session_state.deck_path = deck_path
        st.session_state.support_paths = support_paths
        st.session_state.settings = settings
        st.session_state.send_email = bool(send_email)
        st.session_state.stage = "review"
        st.rerun()


# --------------------------------------------------------------------------
# Stage 2 — review and edit the competitor set
# --------------------------------------------------------------------------


def _render_diagnostics(analysis: CompetitiveAnalysis) -> None:
    d = analysis.diagnostics
    if d.errors:
        for e in d.errors:
            st.error(e, icon="🚫")
    if d.degraded:
        st.warning(
            "This run was **degraded**. The output is still produced, but read the "
            "warnings before relying on it.",
            icon="⚠️",
        )
    if d.warnings:
        with st.expander(f"Warnings ({len(d.warnings)})"):
            for w in d.warnings:
                st.markdown(f"- {w}")
    if d.info:
        with st.expander(f"Run notes ({len(d.info)})"):
            for n in d.info:
                st.markdown(f"- {n}")


if st.session_state.stage == "review":
    analysis: CompetitiveAnalysis = st.session_state.analysis
    settings = st.session_state.get("settings", Settings())

    _render_diagnostics(analysis)

    if not analysis.competitors and analysis.diagnostics.errors:
        st.stop()

    st.markdown("### Company profile")
    p = analysis.profile
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Company", p.display_name())
    c2.metric("Sector", p.sector.display("—"))
    c3.metric("Stage", p.funding_stage.display(p.current_round.display("—")))
    c4.metric("Profile coverage", f"{p.coverage:.0%}")

    with st.expander(f"Extracted fields ({len(p.known_fields())} of 34) and their sources"):
        rows = []
        for name in p.known_fields():
            fact = p.get(name)
            rows.append(
                {
                    "Field": name.replace("_", " "),
                    "Value": fact.display(),
                    "Evidence": fact.evidence_type.value,
                    "Source": fact.badge() or "—",
                    "Confidence": round(fact.confidence, 2),
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)
        missing = p.missing_fields()
        if missing:
            st.caption("Not established: " + ", ".join(m.replace("_", " ") for m in missing))

    if p.extraction_notes:
        with st.expander(f"Analyst notes from extraction ({len(p.extraction_notes)})"):
            for note in p.extraction_notes:
                st.markdown(f"- {note}")

    st.divider()
    st.markdown("### Competitors identified")
    st.caption(
        "Deselect anything that does not genuinely compete for the same customer, "
        "workflow or budget. The score, market map, whitespace and positioning are "
        "recomputed from whatever you keep."
    )

    excluded: set = st.session_state.excluded
    for c in analysis.competitors:
        cols = st.columns([0.06, 0.94])
        with cols[0]:
            keep = st.checkbox(
                "keep", value=c.name not in excluded, key=f"keep_{c.name}",
                label_visibility="collapsed",
            )
        with cols[1]:
            threat = SEVERITY_ICON.get(c.threat_level.value, "")
            deck_tag = " · named in deck" if c.named_in_deck else ""
            st.markdown(
                f"**{c.name}** — {CATEGORY_LABEL[c.category]} {threat} "
                f"`{c.threat_level.value}`{deck_tag}"
            )
            st.caption(
                f"{c.primary_use_case[:200]}  \n"
                f"Funding: {c.funding_raised} · HQ: {c.headquarters} · "
                f"Pricing: {c.pricing} · Confidence: {c.confidence:.2f}"
            )
        if keep:
            excluded.discard(c.name)
        else:
            excluded.add(c.name)
    st.session_state.excluded = excluded

    kept = [c for c in analysis.competitors if c.name not in excluded]
    st.info(f"{len(kept)} of {len(analysis.competitors)} competitors selected.")

    st.divider()
    c1, c2 = st.columns([1, 1])
    with c1:
        recompute = st.button(
            "Recompute analysis with selected competitors",
            use_container_width=True,
            disabled=not excluded,
            help="Only needed if you changed the selection.",
        )
    with c2:
        proceed = st.button("Produce report", type="primary", use_container_width=True)

    if recompute or proceed:
        if excluded:
            from analysis import differentiation, investor_risks
            from analysis import market_map as market_map_module
            from analysis import positioning as positioning_module
            from analysis import whitespace as whitespace_module
            from llm.client import LLMClient

            client = LLMClient(settings)
            d = analysis.diagnostics
            d.note(
                f"Analyst excluded {len(excluded)} competitor(s): "
                + ", ".join(sorted(excluded))
            )
            analysis.competitors = kept

            with st.spinner("Recomputing from the edited competitor set…"):
                analysis.dimensions, assessment = differentiation.assess_dimensions(
                    analysis.profile, kept, client, d
                )
                analysis.score = differentiation.compute_score(
                    analysis.dimensions, analysis.profile, kept, d, settings
                )
                analysis.assessment = assessment or analysis.score.explanation
                analysis.market_map = market_map_module.build(
                    analysis.profile, kept, client, d
                )
                analysis.whitespace = whitespace_module.analyse(
                    analysis.profile, kept, client, d, settings
                )
                analysis.concerns = investor_risks.identify(
                    analysis.profile, kept, analysis.dimensions, analysis.score,
                    client, d, settings,
                )
                analysis.positioning = positioning_module.recommend(
                    analysis.profile, kept, analysis.dimensions, analysis.score, client, d
                )
            st.session_state.excluded = set()

        if proceed or excluded:
            from pipeline import render

            with st.spinner("Rendering the one-page report…"):
                pdf_path, json_path = render(analysis, settings=settings)
            st.session_state.outputs = (pdf_path, json_path)
            st.session_state.analysis = analysis

            # Deliver once, on the way to the report -- not on every recompute.
            if proceed and st.session_state.get("send_email"):
                from pipeline import deliver

                with st.spinner("Emailing the report…"):
                    sent, message = deliver(analysis, pdf_path, json_path, settings)
                st.session_state.delivery = (sent, message)

            if proceed:
                st.session_state.stage = "report"
            st.rerun()


# --------------------------------------------------------------------------
# Stage 3 — report
# --------------------------------------------------------------------------

if st.session_state.stage == "report":
    analysis = st.session_state.analysis
    settings = st.session_state.get("settings", Settings())
    pdf_path, json_path = st.session_state.outputs

    _render_diagnostics(analysis)

    score = analysis.score
    _card(
        theme.score_block(score.total, score.band, score.confidence)
        + f'<p class="tc-lede" style="margin-top:14px;">'
        f'{analysis.assessment or score.explanation}</p>'
        + theme.positioning_block(
            analysis.positioning.recommended or "Not established."
        )
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Competitors", len(analysis.competitors))
    c2.metric("Model calls", analysis.diagnostics.llm_calls)
    c3.metric("Positioning", f"{analysis.positioning.word_count} words")
    st.caption(score.explanation)
    if analysis.positioning.competitive_narrative:
        st.caption(f"**Narrative:** {analysis.positioning.competitive_narrative}")


    c1, c2 = st.columns(2)
    with c1:
        if analysis.positioning.proof_points_required:
            st.markdown("**Proof points required**")
            for x in analysis.positioning.proof_points_required:
                st.markdown(f"- {x}")
    with c2:
        if analysis.positioning.claims_to_avoid:
            st.markdown("**Claims to avoid**")
            for x in analysis.positioning.claims_to_avoid:
                st.markdown(f"- {x}")

    st.markdown("### Competitive matrix")
    st.dataframe(
        [
            {
                "Company": r.company,
                "Customer": r.customer,
                "Product / approach": r.product,
                "Pricing": r.pricing,
                "Traction": r.traction,
                "Distribution": r.distribution,
                "Primary advantage": r.advantage,
                "Threat": r.threat,
            }
            for r in analysis.matrix.rows
        ],
        use_container_width=True,
        hide_index=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### Whitespace")
        for w in analysis.whitespace:
            st.markdown(f"**{w.opportunity}** · `{w.difficulty}`")
            st.caption(f"{w.investor_relevance}  \n_Evidence:_ {w.evidence}")
    with c2:
        st.markdown("### Investor watchlist")
        for c in analysis.concerns:
            st.markdown(f"{SEVERITY_ICON.get(c.severity,'')} **{c.concern}**")
            st.caption(
                f"**Risk:** {c.current_risk}  \n"
                f"**Ask:** {c.diligence_question}  \n"
                f"**Needs:** {c.evidence_needed}"
            )

    delivery = st.session_state.get("delivery")
    if delivery:
        sent, message = delivery
        (st.success if sent else st.warning)(message, icon="📧" if sent else "⚠️")

    st.divider()
    st.markdown("### Deliverables")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.download_button(
            "Download one-page PDF",
            data=Path(pdf_path).read_bytes(),
            file_name=Path(pdf_path).name,
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )
    with c2:
        st.download_button(
            "Download JSON record",
            data=Path(json_path).read_bytes(),
            file_name=Path(json_path).name,
            mime="application/json",
            use_container_width=True,
        )
    with c3:
        # Resend on demand: useful when the automatic send failed, or when the
        # analyst suppressed it and then changed their mind.
        label = "Email report" if not delivery else "Email again"
        if st.button(
            label if settings.email_configured else "Email (not configured)",
            disabled=not settings.email_configured,
            use_container_width=True,
        ):
            from pipeline import deliver as deliver_report

            sent, message = deliver_report(analysis, pdf_path, json_path, settings)
            st.session_state.delivery = (sent, message)
            (st.success if sent else st.error)(message)
    with c4:
        if analysis.consent.external_transmission_permitted:
            if st.button("Send to CRM", use_container_width=True):
                from reporting.record import transmit

                sent, message = transmit(analysis, settings)
                (st.success if sent else st.error)(message)
        else:
            st.button(
                "Send to CRM (not authorised)", disabled=True, use_container_width=True
            )

    if st.button("← Back to competitor selection"):
        st.session_state.stage = "review"
        st.rerun()

st.markdown(theme.footer(), unsafe_allow_html=True)
