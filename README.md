# Competitive Intelligence One-Pager Generator

Turns an investor pitch deck (PDF or PPTX) into a **one-page competitive
intelligence report** for investors, plus a **structured JSON record** for the
TEN Capital CRM.

It answers four questions an investor needs before a partner meeting:

> Who does this company really compete with, why might it win, how durable is
> that advantage, and what should I investigate before investing?

---

## What makes this different from a summariser

The system is built around one idea: **an investor-facing document must never
launder a claim into a fact.** Three mechanisms enforce that.

**1. Provenance is part of the type.** Every extracted value is a `Fact`
carrying its source (`Slide 6`), its epistemic status (`stated_fact`,
`company_claim`, `analyst_inference`, `unverified`, `missing`) and a calibrated
confidence. Nothing can be rendered without the chance to render where it came
from.

**2. The model judges; Python scores.** The model assesses 18 competitive
dimensions qualitatively. It is never asked for the score. The 0–100
Differentiation Score is computed deterministically from those assessments with
visible weights in [`config.py`](config.py), so the arithmetic is reproducible
and arguable. Unevidenced claims are pulled toward a neutral baseline rather
than rewarded — a test (`test_unevidenced_claims_do_not_earn_points`) pins that
behaviour:

| Assessment                              | Score | Band                        |
| --------------------------------------- | ----: | --------------------------- |
| Advantage everywhere, evidenced, durable |    98 | Highly differentiated       |
| Advantage everywhere, **claimed only**   |    51 | Moderate differentiation    |
| Nothing established                      |    38 | Weak differentiation        |
| Behind everywhere, evidenced             |    16 | Commodity / highly vulnerable |

Asserting an advantage on all 18 dimensions with no evidence buys 13 points
over saying nothing at all. That gap is the design.

**3. Missing data is an output.** Competitor financials default to
`Not publicly disclosed` and qualitative fields to `Unknown`. Hedged figures
("approximately $50M") are rejected for financial fields — a hedged invention is
still an invention. Nothing in the pipeline may fill these in by inference.

---

## Install

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows;  source .venv/bin/activate on POSIX
pip install -r requirements.txt

cp .env.example .env             # then add your ANTHROPIC_API_KEY
```

Python 3.10+ (developed on 3.14).

### Without an API key

The app still runs, using deterministic keyword extraction. It will **not**
perform competitor discovery or competitive assessment, every run is marked
`DEGRADED RUN` on the page itself, and the CLI exits `1`. That is deliberate: a
degraded report must be impossible to mistake for a full one.

---

## Use

### Command line

```bash
# Simplest form
python cli.py samples/sample_pitch_deck.pptx

# With public research across the approved domain allowlist
python cli.py deck.pdf --research

# Supplying what the deck leaves out
python cli.py deck.pdf --research \
    --company "Acme Health" --sector "Digital Health" \
    --customers "Mid-market hospital systems" \
    --competitors "Epic, Cerner" \
    --url https://www.sec.gov/some/filing \
    --doc research/market_note.pdf

# Deterministic, offline, no model calls
python cli.py deck.pptx --no-llm

# Suppress the automated email for one run, or redirect it
python cli.py deck.pdf --no-email
python cli.py deck.pdf --email-to partner@example.com

# Verify email delivery without any model call or API cost
python cli.py --email-test
```

Exit codes: `0` report produced · `1` produced but degraded · `2` nothing produced.

### Streamlit

```bash
streamlit run app.py
```

The interface carries the TEN Capital Network brand — the tri-colour mark, Sora
/ Inter / JetBrains Mono, and the coral→amber call to action — defined once in
[`ui/theme.py`](ui/theme.py). The palette lives there as data, the stylesheet is
generated from it, and the same navy and tri-colour signature rule carry onto
the PDF, so the interface and the deliverable read as one product.

Two pieces of interface copy are driven by application state rather than
written into the markup: the accepted file formats come from the ingestion
layer, and the delivery address from `Settings`. Neither can go stale, and the
interface cannot promise a format the pipeline would reject.

**Brand assets.** `assets/ten_capital_mark.png` is the master; `static/` is the
public directory holding the generated favicon set (16/32/48/180/192/512 PNG
plus a multi-resolution `favicon.ico`), served at `/app/static/<file>`. The tab
icon is set through `st.set_page_config(page_icon=...)` — Streamlit strips
injected `<link rel="icon">` tags, so that is the only mechanism that actually
works. Regenerate the whole set from a new master with:

```bash
python -m ui.assets path/to/new-logo.png
```

The palette in `ui/theme.py` is sampled from the mark itself rather than from a
mockup, and a test fails if the two ever drift apart.

Deployed as a web app on Railway — see **[DEPLOY.md](DEPLOY.md)**. Set
`ANTHROPIC_API_KEY` and `APP_PASSWORD` as service variables; everything else
has a working default.

Upload → optional details → consent → generate. The app then **pauses on the
competitor set** so you can deselect false competitors before the score, market
map, whitespace and positioning are computed. Editing the set re-runs everything
downstream of it, so the score always matches the competitors shown.

---

## Output

**`<company>_competitive_intelligence_<date>.pdf`** — US Letter, portrait,
**exactly one page**:

| Section | Contents |
| --- | --- |
| Header | Company, sector, stage, date, evidence base |
| 1 · Competitive position | Differentiation score, band, confidence, assessment, recommended positioning |
| 2 · Competitive matrix | Subject + 4–7 competitors across customer, product, pricing, traction, distribution, advantage, threat |
| 3 · Market map & whitespace | Two-axis positioning chart + top 3 opportunities |
| 4 · Investor watchlist | 3–5 risks, each as *risk today · question to ask · evidence that settles it* |
| Footer | Source legend, TEN Capital attribution, confidentiality notice |

**An automated email** to the configured recipient (`Info@tencapital.group` by
default), carrying the findings in the body — score, band, confidence,
recommended positioning, the competitive set with threat levels, and the
investor watchlist — with the PDF and JSON attached. A recipient reading it on a
phone gets the same judgement the PDF gives. Sent via Resend; see
[DEPLOY.md](DEPLOY.md). Delivery is best-effort by design: a failed send is a
recorded warning, never a lost report.

**`<company>_competitive_intelligence_<date>.json`** — the full audit trail:
every profile field with its sources, all competitors, all 18 dimension
assessments, category scores, whitespace, risks, diligence questions, consent
state and run diagnostics. See [`samples/example_output/`](samples/example_output/).

## Document structure template

The canonical, company-free skeleton of every report the app produces lives in
[`samples/template/`](samples/template/):

| File | What it is |
| --- | --- |
| `competitive_intelligence_template.pdf` | The blank one-pager — every section, column and field as a bracketed placeholder |
| `competitive_intelligence_template.json` | The record skeleton, same keys as a real record, placeholders for values |
| `DOCUMENT_STRUCTURE.md` | Field-by-field specification: each field, the model attribute it comes from, and the rule that governs it |

Regenerate at any time — no deck, no API key, no cost:

```bash
python cli.py --template                 # writes to samples/template/
python cli.py --template -o path/to/dir
```

**The template is rendered through the same `generate_pdf` pipeline as a real
report**, from a `CompetitiveAnalysis` filled entirely with placeholders. There
is no second layout implementation, so adding a column or reordering a section
updates the template automatically. A test
(`test_template_has_the_same_sections_as_a_real_report`) pins the two together,
and others assert the template leaks no company content, no real score and no
real date — a stale or half-blank template is worse than none.

The structural contract itself is data, in `STRUCTURE` in
[`reporting/template.py`](reporting/template.py); the Markdown spec is rendered
from it rather than maintained by hand.

---

### How one page is guaranteed

Content length is unknown until render time, so the generator **renders,
measures, and retries** down a ladder of layout budgets in
[`reporting/layout.py`](reporting/layout.py), following the spec's priority
order:

1. shorten the narrative
2. reduce competitors shown (never below 4)
3. drop lower-value sections — the market map goes before the watchlist
4. never overflow

`require()` reserves each section's height *before* drawing, so overflow is
detected rather than discovered. Every reduction is reported to the user. If no
rung fits, a file is still written, clipped, with a loud error — the analyst is
never left empty-handed *or* misled.

---

## Architecture

```
├── app.py                  Streamlit interface (password-gated when APP_PASSWORD is set)
├── Procfile                Railway / Nixpacks start command
├── railway.toml            Service config: start command, health check, replicas
├── .streamlit/config.toml  Server settings for running behind Railway's proxy
├── cli.py                  Command-line interface
├── pipeline.py             Orchestration — the one place stages are wired together
├── config.py               Every tunable: model, weights, allowlist, page budget
│
├── ingestion/              pdf_reader · pptx_reader · normalizer
├── extraction/             company_extractor · evidence
├── research/               competitor_discovery · competitor_enrichment · source_manager
├── analysis/               competitor_classifier · differentiation · market_map
│                           whitespace · investor_risks · positioning
├── reporting/              pdf_generator · charts · layout · record · template
├── llm/                    client — the single point of model contact
├── notifications/          email_sender — automated delivery via Resend
├── ui/                     theme — brand tokens, CSS and components
│                           assets — the mark, favicon set and its generator
├── assets/                 ten_capital_mark.png — the brand master
├── static/                 public directory: favicon set, served at /app/static
├── models/schemas.py       The evidence-first data model
├── samples/                Sample deck generator, example output, document template
└── tests/                  243 tests, fully offline
```

Design decisions worth knowing:

- **One LLM choke point.** Every model call goes through
  `LLMClient.structured()` with a Pydantic output schema. There is no free-text
  call path and no regex-parsing of prose anywhere. Grounding rules attach to
  every call by construction, and cost, retries, timeouts and degradation are
  handled once.
- **Discovery is separate from enrichment.** Reasoning about *who* competes is
  legitimate analyst work; asserting a competitor's *revenue* is a factual claim
  that must be sourced or withheld. Different epistemics, different modules.
- **Two front ends, one orchestrator.** The CLI and Streamlit app are thin
  shells over `pipeline.analyse` / `pipeline.render`, so they cannot drift apart
  and the whole pipeline is testable without either.
- **Every stage degrades rather than raises.** Failures land in
  `RunDiagnostics` and surface in the CLI summary, the Streamlit UI, the PDF
  header and the JSON record.

---

## Competitor discovery

Competitors are found across five categories — **direct, indirect, incumbent,
emerging, substitute** — using the company's problem, customer, buyer, workflow,
business model and geography.

Two rules do most of the work:

- **The deck's competitor slide is an input, never the answer.** Founders name
  flattering comparisons; the interesting competitors are the ones missing.
- **Substitutes are first-class competitors.** In-house staffing, outsourcing,
  spreadsheets, the incumbent's existing module, and doing nothing. For most
  early-stage B2B companies the status quo is what actually wins deals. If no
  substitute is found, the run warns — treat its absence as a gap in the
  analysis, not as good news.

`analysis/competitor_classifier.py` then deduplicates, filters companies that
share a sector but never appear in the same purchase decision, and — when
trimming to fit — keeps one representative per category so the only substitute
or the only incumbent is never silently dropped.

---

## Research and consent

Nothing reaches the network unless **three independent gates** all pass:

1. **Operator authorisation** — `CI_RESEARCH_ENABLED`
2. **Per-run user consent** — `Consent.public_research_permitted`
3. **Domain allowlist** — `CI_SOURCE_ALLOWLIST`

Refusals are recorded, so a report produced without research is visibly
different from one where research found nothing. Some publishers block the
research crawler; those domains are dropped and the call retried rather than
failing the run.

**Transmission to a CRM is a separate, stricter gate**: it needs explicit user
authorisation *and* a configured `TEN_CRM_ENDPOINT`. Absent either, nothing
leaves the machine. Writing files to disk is not transmission.

---

## Configuration

All settings live in [`config.py`](config.py) and are overridable by
environment variable — see [`.env.example`](.env.example). The most useful:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | Model access; absent ⇒ heuristic mode |
| `CI_MODEL` | `claude-opus-5` | This output informs investment decisions, so quality outranks per-run cost |
| `CI_EFFORT` | `high` | `low`–`max`; raise for complex markets, lower to cut cost |
| `CI_MATRIX_ROWS` | `6` | Competitors in the one-page matrix (4–7) |
| `CI_RESEARCH_ENABLED` | `true` | Operator gate; per-run consent still required |
| `CI_SOURCE_ALLOWLIST` | curated list | Approved research domains |
| `TEN_CRM_ENDPOINT` | unset | Unset ⇒ transmission is impossible |
| `APP_PASSWORD` | unset | Gates the deployed UI; unset shows a public-instance warning |
| `RESEND_API_KEY` | unset | Unset ⇒ no email is sent; everything else works unchanged |
| `EMAIL_TO` | `Info@tencapital.group` | Comma-separated for several recipients |
| `EMAIL_FROM` | `reports@tencapital.group` | Must be on a Resend-verified domain |

A full run makes 8 model calls (extraction, discovery, enrichment, dimensions,
map, whitespace, concerns, positioning), using server-side web search when
research is authorised.

---

## Tests

```bash
python -m pytest tests/ -q          # 243 tests, ~9 seconds
```

The suite runs **entirely offline** — a session fixture strips model
credentials, so no test can silently bill the API or depend on network
conditions. Model-backed paths are covered by injecting a `FakeLLM` returning
canned, schema-valid payloads, which is what makes the mapping between model
output and internal models testable.

Coverage spans PDF and PPTX extraction (including speaker notes, tables and
corrupt files), profile extraction and merge precedence, evidence grading,
competitor classification, the scoring calibration curve, one-page enforcement
and the full reduction ladder, JSON schema conformance, research and
transmission gating, end-to-end degraded runs, the document template, email
composition and delivery failure handling, and the deployment configuration —
including a guard that fails if an API key is ever committed.

```bash
python samples/make_sample_deck.py   # regenerate the sample deck pair
```

---

## Limitations

- **Image-only decks** yield almost nothing. The run reports this loudly rather
  than inventing content; supply details manually or OCR the deck first.
- **Competitor financials** are only as good as public disclosure. Most private
  competitors will legitimately show `Not publicly disclosed`.
- **The score is a screening instrument, not a verdict.** Read it alongside its
  confidence value; below 0.5 it is provisional pending diligence.
- **Legacy `.ppt` and Keynote** are not read directly — export to PDF or PPTX.
