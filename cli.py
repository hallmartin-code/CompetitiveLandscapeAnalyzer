"""
Command-line interface.

    python cli.py samples/sample_pitch_deck.pptx --research --company "Kestrel"

Exit codes:
    0  report produced
    1  report produced but the run was degraded (missing model, failed research,
       content reduced to fit the page)
    2  no report could be produced
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from config import Settings
from models.schemas import Consent, UserInputs


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="competitive-intelligence",
        description="Generate a one-page competitive intelligence report from a pitch deck.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "deck", nargs="?", default=None, help="Path to the pitch deck (.pdf or .pptx)"
    )
    p.add_argument(
        "--template",
        action="store_true",
        help="Emit the blank document structure template (PDF, JSON and Markdown spec) "
             "instead of analysing a deck. Requires no deck and no model.",
    )
    p.add_argument(
        "-o", "--output-dir", default=None, help="Where to write the PDF and JSON record"
    )
    p.add_argument("--doc", action="append", default=[], metavar="PATH",
                   help="Supporting document (repeatable)")
    p.add_argument("--url", action="append", default=[], metavar="URL",
                   help="Approved source URL (repeatable; allowlist enforced)")

    g = p.add_argument_group("company details (optional; derived from the deck if omitted)")
    g.add_argument("--company", help="Company name")
    g.add_argument("--website")
    g.add_argument("--sector")
    g.add_argument("--subsector")
    g.add_argument("--product", help="Product description")
    g.add_argument("--customers", help="Target customers")
    g.add_argument("--geography")
    g.add_argument("--business-model")
    g.add_argument("--pricing")
    g.add_argument("--competitors", help="Known competitors, comma separated")
    g.add_argument("--stage", help="Funding stage")
    g.add_argument("--raise-amount")
    g.add_argument("--traction")
    g.add_argument("--notes", help="Analyst notes")

    c = p.add_argument_group("consent and research")
    c.add_argument("--research", action="store_true",
                   help="Authorise public research across the approved domain list")
    c.add_argument("--consent", choices=["granted", "declined", "not_provided"],
                   default=None, help="Explicit consent status (implied 'granted' by --research)")
    c.add_argument("--transmit", action="store_true",
                   help="Authorise sending the record to TEN_CRM_ENDPOINT after generation")

    e = p.add_argument_group("email delivery")
    e.add_argument("--no-email", action="store_true",
                   help="Suppress the automated report email for this run")
    e.add_argument("--email-to", action="append", default=[], metavar="ADDRESS",
                   help="Override the recipient (repeatable)")
    e.add_argument("--email-test", action="store_true",
                   help="Send a test email using the blank document template and exit. "
                        "Verifies Resend credentials, sender domain and attachments "
                        "without making any model call or incurring API cost.")

    m = p.add_argument_group("model")
    m.add_argument("--model", default=None, help="Override the model id")
    m.add_argument("--effort", default=None, choices=["low", "medium", "high", "xhigh", "max"])
    m.add_argument("--no-llm", action="store_true",
                   help="Run heuristics only, without any model call")

    p.add_argument("--matrix-rows", type=int, default=None,
                   help="Competitors shown in the matrix (4-7)")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def _force_utf8_console() -> None:
    """Make the summary printable on a legacy Windows console.

    The default Windows console codepage (cp1252) cannot encode the box-drawing
    and typographic characters used in the summary, and the resulting
    UnicodeEncodeError would kill the process *after* a successful, and not
    cheap, analysis run. Reconfiguring with errors='replace' means a console
    that cannot render a character degrades to '?' instead of losing the report.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else (logging.ERROR if args.quiet else logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )
    # The HTTP client is chatty at INFO; it drowns out our own progress.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.email_test:
        return _send_test_email(args)

    if args.template:
        from reporting.template import write_all

        target = Path(args.output_dir) if args.output_dir else Path("samples/template")
        written = write_all(target, Settings())
        print("Document structure template written:")
        for kind, path in written.items():
            print(f"  {kind:<9} {path}")
        return 0

    if args.deck is None:
        print("error: a pitch deck is required (or use --template)", file=sys.stderr)
        return 2

    deck = Path(args.deck)
    if not deck.exists():
        print(f"error: pitch deck not found: {deck}", file=sys.stderr)
        return 2

    settings = Settings()
    if args.model:
        settings.model = args.model
    if args.effort:
        settings.effort = args.effort
    if args.matrix_rows:
        settings.matrix_rows = max(4, min(7, args.matrix_rows))
    if args.email_to:
        settings.email_to = list(args.email_to)
    if args.no_llm:
        # Force the deterministic path by removing the credential from this
        # Settings instance only; the environment is left untouched.
        settings.api_key = None
        import os

        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
            os.environ.pop(var, None)

    consent = Consent()
    status = args.consent or ("granted" if args.research else "not_provided")
    if status == "granted":
        consent.grant(public_research=args.research)
    else:
        consent.status = status  # type: ignore[assignment]
    consent.external_transmission_permitted = bool(args.transmit)

    user = UserInputs(
        company_name=args.company,
        website=args.website,
        sector=args.sector,
        subsector=args.subsector,
        product_description=args.product,
        target_customers=args.customers,
        geography=args.geography,
        business_model=args.business_model,
        pricing=args.pricing,
        known_competitors=args.competitors,
        funding_stage=args.stage,
        raise_amount=args.raise_amount,
        traction=args.traction,
        notes=args.notes,
        source_urls=list(args.url),
    )

    def progress(i: int, n: int, label: str) -> None:
        if not args.quiet:
            print(f"[{i:>2}/{n}] {label}...", flush=True)

    from pipeline import run_analysis

    analysis, pdf_path, json_path = run_analysis(
        deck,
        user=user,
        consent=consent,
        supporting_paths=args.doc,
        settings=settings,
        output_dir=args.output_dir,
        progress=progress,
        email=not args.no_email,
    )

    d = analysis.diagnostics
    if pdf_path is None:
        print("\nNo report could be produced.", file=sys.stderr)
        for err in d.errors:
            print(f"  error: {err}", file=sys.stderr)
        return 2

    if args.transmit:
        from reporting.record import transmit as transmit_record

        sent, message = transmit_record(analysis, settings)
        print(("  " if args.quiet else "\n") + message)
        if not sent:
            d.warn(message)

    if not args.quiet:
        _print_summary(analysis, pdf_path, json_path)

    return 1 if (d.errors or d.degraded) else 0


def _send_test_email(args) -> int:
    """Verify email delivery end to end, without touching the model API."""
    import tempfile

    from notifications.email_sender import send_report
    from reporting.template import build_template_analysis, write_all

    settings = Settings()
    if args.email_to:
        settings.email_to = list(args.email_to)

    if not settings.resend_api_key:
        print("error: RESEND_API_KEY is not configured.", file=sys.stderr)
        return 2
    if not settings.email_to:
        print("error: no recipient configured (EMAIL_TO).", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        written = write_all(tmp, settings)
        analysis = build_template_analysis(settings)
        sent, message = send_report(
            analysis,
            written["pdf"],
            written["json"],
            settings,
            analysis.diagnostics,
            subject_prefix="[TEST] ",
        )

    print(f"  from : {settings.email_from}")
    print(f"  to   : {', '.join(settings.email_to)}")
    print(f"  {'SENT' if sent else 'FAILED'}: {message}")
    return 0 if sent else 1


def _print_summary(analysis, pdf_path: Path, json_path: Path) -> None:
    from models.schemas import CATEGORY_LABEL

    a = analysis
    d = a.diagnostics
    line = "─" * 78

    print(f"\n{line}")
    print(f"  {a.profile.display_name()}")
    print(f"  Differentiation {a.score.total}/100 — {a.score.band}  "
          f"(confidence {a.score.confidence:.2f})")
    print(line)

    print(f"\n  Competitors assessed: {len(a.competitors)}")
    counts: dict = {}
    for c in a.competitors:
        counts[c.category] = counts.get(c.category, 0) + 1
    if counts:
        print("    " + "   ".join(f"{CATEGORY_LABEL[k]}: {v}" for k, v in counts.items()))
    for c in a.selected_competitors(6):
        print(f"    · {c.name[:44]:<44} {c.category.value:<10} threat={c.threat_level.value}")

    if a.positioning.recommended:
        print(f"\n  Recommended positioning ({a.positioning.word_count} words):")
        print(f"    {a.positioning.recommended}")

    if a.concerns:
        print("\n  Investor watchlist:")
        for c in a.concerns[:5]:
            print(f"    [{c.severity.upper():<6}] {c.concern}")

    if a.whitespace:
        print("\n  Whitespace:")
        for w in a.whitespace[:4]:
            print(f"    · ({w.difficulty}) {w.opportunity}")

    if d.errors:
        print("\n  ERRORS:")
        for e in d.errors:
            print(f"    ! {e}")
    if d.warnings:
        print("\n  WARNINGS:")
        for w in d.warnings:
            print(f"    - {w}")

    # Delivery outcome, drawn from the diagnostics the sender wrote.
    delivered = [n for n in d.info if n.startswith("Report emailed")]
    failed = [w for w in d.warnings if w.startswith("Email delivery failed")]
    if delivered:
        print(f"\n  EMAIL: {delivered[0]}")
    elif failed:
        print(f"\n  EMAIL: {failed[0]}")

    print(f"\n  PDF:  {pdf_path}")
    print(f"  JSON: {json_path}")
    if d.degraded:
        print("\n  NOTE: this run was degraded — see warnings above before relying on the output.")
    print()


if __name__ == "__main__":
    sys.exit(main())
