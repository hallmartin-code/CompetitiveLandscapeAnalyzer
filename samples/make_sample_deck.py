"""
Generates the development sample dataset: a synthetic investor pitch deck for a
fictional company, emitted as both PPTX and PDF.

The deck is deliberately *realistic rather than ideal*. It contains:
  - solid, sourced claims (a named pilot, a filed patent)
  - unsourced company claims ("10x faster") that the scorer must discount
  - genuine gaps (no pricing detail, no named lead investor)
  - a competitor slide that names only two flattering comparisons

That mix is what exercises the interesting paths: evidence grading, gap
reporting, and competitor discovery that must go beyond the deck's own list.

Run:  python samples/make_sample_deck.py
"""

from __future__ import annotations

import sys
from pathlib import Path

SAMPLE_DIR = Path(__file__).resolve().parent
COMPANY = "Kestrel Authorization Systems"

# (title, bullet lines, speaker notes)
SLIDES: list[tuple[str, list[str], str]] = [
    (
        f"{COMPANY}",
        [
            "KestrelPA — autonomous prior authorization for specialty clinics",
            "Seed round | Austin, TX | kestrelauth.example",
            "Confidential — for discussion purposes only",
        ],
        "Founded 2023. Two clinical ops founders out of a large ortho group.",
    ),
    (
        "The Problem",
        [
            "Prior authorization consumes 13.7 hours per physician per week (AMA 2024 survey)",
            "94% of physicians report care delays caused by PA; 24% report serious adverse events",
            "Average specialty clinic employs 1 FTE per 3 providers purely for authorization work",
            "Denials on first submission run 18-22% industry-wide, driving costly rework",
        ],
        "Sourcing: AMA 2024 prior auth physician survey. The FTE ratio is from our own "
        "customer discovery across 40 clinics, not a published statistic.",
    ),
    (
        "Why Now",
        [
            "CMS Interoperability and Prior Authorization Final Rule (CMS-0057-F) mandates",
            "  electronic PA APIs for impacted payers by January 2027",
            "Payer FHIR endpoints becoming available creates a machine-readable submission path",
            "Staffing shortages make the manual approach structurally unsustainable",
        ],
        "The CMS rule is the wedge. Payers must expose APIs; nobody has built the clinic-side "
        "workflow layer on top of them for specialty practices.",
    ),
    (
        "Our Solution",
        [
            "KestrelPA reads the chart, assembles the clinical packet, and submits to the payer",
            "Payer-specific rules engine covering 240 plans across ortho, GI and cardiology",
            "Submits via FHIR where available, falls back to portal automation elsewhere",
            "Clinic staff review an exception queue instead of doing data entry",
        ],
        "The rules engine is the actual asset. Portal fallback is unglamorous but it is why "
        "we work today rather than in 2027.",
    ),
    (
        "Product Traction",
        [
            "8 paying specialty clinics; 3 in ortho, 3 GI, 2 cardiology",
            "142,000 authorizations processed since January 2025",
            "First-pass approval rate of 91% vs 79% clinic baseline (measured across 4 pilots)",
            "Median turnaround 4.2 hours vs 3.1 days pre-deployment",
            "Net revenue retention 118% over trailing 6 months",
        ],
        "The 91% vs 79% is our strongest number and it is measured, not modelled. "
        "Turnaround comparison is from the same four pilot sites.",
    ),
    (
        "Business Model",
        [
            "SaaS subscription per provider per month, plus per-authorization usage fee",
            "Land with a single specialty line, expand across the practice",
            "Current ARR $840K; average contract value $105K",
            "Gross margin 71% and improving as portal automation gives way to FHIR",
        ],
        "We do not publish list pricing. Deals are negotiated per practice. "
        "That is a gap we know investors will push on.",
    ),
    (
        "Technology & Defensibility",
        [
            "Proprietary payer rules corpus: 240 plans, updated from 142K live submissions",
            "US patent application filed (17/xxx,xxx) covering adaptive packet assembly",
            "Every denial is labelled training data — the corpus compounds with volume",
            "SOC 2 Type II complete; HIPAA BAAs in place with all customers",
        ],
        "Patent is filed, not granted. Do not let anyone leave the room thinking it is granted.",
    ),
    (
        "Competitive Landscape",
        [
            "Legacy clearinghouses (Availity, Waystar) — transaction plumbing, not clinical workflow",
            "Point solutions focused on payer side rather than the specialty clinic",
            "KestrelPA is 10x faster than the manual process and far easier to adopt",
            "We are the only solution purpose-built for the specialty clinic exception queue",
        ],
        "This slide is thin and we know it. The honest answer is that Cohere and Rhyme are "
        "real, and the incumbents could move down-market.",
    ),
    (
        "Go To Market",
        [
            "Direct sales to practice administrators; 62-day average sales cycle",
            "Two channel conversations underway with specialty-focused EHR vendors",
            "Specialty society conferences as primary top-of-funnel",
        ],
        "Channel is the leverage point but nothing is signed yet.",
    ),
    (
        "Team",
        [
            "CEO — 11 years running revenue cycle operations for a 40-provider ortho group",
            "CTO — former staff engineer, healthcare interoperability platform",
            "VP Clinical Ops — RN, certified professional coder",
            "9 FTE total; hiring two payer integration engineers",
        ],
        "No full-time sales leader yet. CEO is still carrying quota.",
    ),
    (
        "The Ask",
        [
            "Raising $6M seed to reach 40 clinics and $4M ARR",
            "Use of proceeds: 45% engineering, 30% go-to-market, 25% payer integrations",
            "18 months of runway at planned burn",
            "Round is open; lead not yet committed",
        ],
        "No lead yet. That is the single biggest thing this round needs.",
    ),
]


def build_pptx(out: Path) -> Path:
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    for title, bullets, notes in SLIDES:
        slide = prs.slides.add_slide(prs.slide_layouts[5])  # title only
        slide.shapes.title.text = title

        box = slide.shapes.add_textbox(Inches(0.8), Inches(1.8), Inches(11.7), Inches(4.8))
        tf = box.text_frame
        tf.word_wrap = True
        for i, line in enumerate(bullets):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.text = line
            para.font.size = Pt(18)

        if notes:
            slide.notes_slide.notes_text_frame.text = notes

    # A table slide, so table extraction is exercised by the sample.
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Unit Economics"
    rows, cols = 5, 3
    table = slide.shapes.add_table(rows, cols, Inches(0.8), Inches(1.8), Inches(9.0), Inches(3.0)).table
    data = [
        ["Metric", "Current", "Target at $4M ARR"],
        ["Average contract value", "$105,000", "$100,000"],
        ["Gross margin", "71%", "78%"],
        ["CAC payback", "14 months", "9 months"],
        ["Net revenue retention", "118%", "125%"],
    ]
    for r, row in enumerate(data):
        for c, val in enumerate(row):
            table.cell(r, c).text = val

    prs.save(str(out))
    return out


def build_pdf(out: Path) -> Path:
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas as pdfcanvas

    page = landscape(letter)
    c = pdfcanvas.Canvas(str(out), pagesize=page)
    c.setTitle(f"{COMPANY} — Seed Deck")
    width, height = page

    for title, bullets, _notes in SLIDES:
        c.setFont("Helvetica-Bold", 24)
        c.drawString(0.8 * inch, height - 1.1 * inch, title)
        c.setFont("Helvetica", 13)
        y = height - 1.8 * inch
        for line in bullets:
            for wrapped in _wrap(line, 92):
                c.drawString(0.9 * inch, y, wrapped)
                y -= 0.30 * inch
            y -= 0.08 * inch
        c.showPage()

    # Table page
    c.setFont("Helvetica-Bold", 24)
    c.drawString(0.8 * inch, height - 1.1 * inch, "Unit Economics")
    c.setFont("Helvetica", 13)
    y = height - 1.9 * inch
    for row in [
        ("Metric", "Current", "Target at $4M ARR"),
        ("Average contract value", "$105,000", "$100,000"),
        ("Gross margin", "71%", "78%"),
        ("CAC payback", "14 months", "9 months"),
        ("Net revenue retention", "118%", "125%"),
    ]:
        c.drawString(0.9 * inch, y, row[0])
        c.drawString(4.6 * inch, y, row[1])
        c.drawString(7.2 * inch, y, row[2])
        y -= 0.34 * inch
    c.showPage()
    c.save()
    return out


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def main() -> int:
    pptx_path = build_pptx(SAMPLE_DIR / "sample_pitch_deck.pptx")
    pdf_path = build_pdf(SAMPLE_DIR / "sample_pitch_deck.pdf")
    print(f"Wrote {pptx_path}")
    print(f"Wrote {pdf_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
