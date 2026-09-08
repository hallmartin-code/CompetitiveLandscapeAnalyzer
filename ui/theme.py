"""
TEN Capital brand theme for the Streamlit interface.

ARCHITECTURAL DECISION -- tokens in Python, not scattered through CSS strings
-----------------------------------------------------------------------------
The palette and type scale live in `BRAND` as data. The stylesheet is generated
from it, and the components below read from it. That means a brand change is
one edit, and it also lets the *content* of the chrome be driven by real
application state -- the accepted file types come from the ingestion layer, and
the delivery address from `Settings` -- rather than being copy pasted into
markup where they quietly go stale.

Streamlit's own class names (`.st-emotion-cache-*`) are build-specific and
change between releases, so every selector here targets a stable
`data-testid` or a semantic element instead. A missed selector degrades to
Streamlit's default dark theme rather than breaking the page.

Accessibility: every foreground/background pair in `BRAND` meets WCAG AA for
its intended size. `ink_600` was lightened from the original mockup's #5C6E86
(3.51:1) to #6E8098 (4.53:1) so the footer clears AA for normal text.
"""

from __future__ import annotations

from typing import Dict, List, Optional

# --------------------------------------------------------------------------
# Brand tokens
# --------------------------------------------------------------------------

BRAND: Dict[str, str] = {
    # Surfaces, darkest to lightest.
    "navy_950": "#0B1526",
    "navy_900": "#101E33",
    "navy_800": "#16283F",
    "navy_700": "#1E354F",
    # The three figures of the mark. Sampled from the brand asset itself
    # (assets/ten_capital_mark.png) rather than from the design mockup, so the
    # interface matches the logo it sits beside. All three clear WCAG AA on
    # both surfaces; the authentic teal is in fact the stronger of the two
    # candidates (8.12:1 against navy-900, versus 7.35:1 for the mockup's).
    "coral": "#ED5644",
    "coral_soft": "#F17A69",
    "amber": "#F1A31F",
    "teal": "#4FC4D6",
    # Text.
    "ink_100": "#F3F6FA",
    "ink_300": "#C4D0E0",
    "ink_500": "#7E90A8",
    "ink_600": "#6E8098",   # lightened from the mockup to clear WCAG AA
    "cta_ink": "#17130E",
}

FONT_DISPLAY = "'Sora', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
FONT_BODY = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
FONT_MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace"

GOOGLE_FONTS = (
    "https://fonts.googleapis.com/css2?"
    "family=Sora:wght@400;600;700;800&"
    "family=Inter:wght@400;500;600&"
    "family=JetBrains+Mono:wght@400;500&display=swap"
)

#: Severity and threat colours, mapped onto the brand's three figures.
SEVERITY = {"high": BRAND["coral"], "medium": BRAND["amber"], "low": BRAND["teal"]}


def score_colour(total: int) -> str:
    """Score colour on the brand scale: teal good, amber middling, coral poor."""
    if total >= 65:
        return BRAND["teal"]
    if total >= 50:
        return BRAND["amber"]
    return BRAND["coral"]


# --------------------------------------------------------------------------
# The logo mark
# --------------------------------------------------------------------------

#: A geometric stand-in for the mark, used only if the asset is unavailable.
#: The real logo (assets/ten_capital_mark.png) is preferred wherever it loads.
LOGO_FALLBACK_SVG = f"""
<svg class="tc-mark" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg"
     role="img" aria-label="TEN Capital Network">
  <circle cx="50" cy="50" r="38" stroke="{BRAND['coral']}" stroke-width="10"
          fill="none" stroke-dasharray="60 20"/>
  <circle cx="34" cy="34" r="9" fill="{BRAND['coral']}"/>
  <circle cx="68" cy="50" r="9" fill="{BRAND['amber']}"/>
  <circle cx="42" cy="68" r="9" fill="{BRAND['teal']}"/>
</svg>
"""


# --------------------------------------------------------------------------
# Stylesheet
# --------------------------------------------------------------------------


def stylesheet() -> str:
    """The full CSS layer, generated from the brand tokens."""
    b = BRAND
    return f"""
<style>
@import url('{GOOGLE_FONTS}');
:root {{
  --navy-950:{b['navy_950']}; --navy-900:{b['navy_900']};
  --navy-800:{b['navy_800']}; --navy-700:{b['navy_700']};
  --coral:{b['coral']}; --coral-soft:{b['coral_soft']};
  --amber:{b['amber']}; --teal:{b['teal']};
  --ink-100:{b['ink_100']}; --ink-300:{b['ink_300']};
  --ink-500:{b['ink_500']}; --ink-600:{b['ink_600']};
}}

/* ---- canvas ---------------------------------------------------------- */
.stApp {{
  background: var(--navy-950);
  color: var(--ink-100);
  font-family: {FONT_BODY};
}}

/* The ambient tri-colour glow, echoing the mark's three figures. Fixed and
   non-interactive so it never intercepts a click. */
.stApp::before {{
  content:""; position:fixed; inset:0; pointer-events:none; z-index:0;
  background:
    radial-gradient(480px 380px at 14% 8%,  rgba(238,90,78,0.16), transparent 60%),
    radial-gradient(480px 380px at 86% 6%,  rgba(243,162,42,0.13), transparent 60%),
    radial-gradient(560px 420px at 50% 100%, rgba(53,190,187,0.14), transparent 60%);
}}
.stApp > * {{ position:relative; z-index:1; }}

.block-container {{ padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1180px; }}
header[data-testid="stHeader"] {{ background: transparent; }}
#MainMenu, footer {{ visibility: hidden; }}

/* ---- typography ------------------------------------------------------ */
h1, h2, h3, h4 {{ font-family:{FONT_DISPLAY} !important; color: var(--ink-100);
                  letter-spacing:-0.01em; }}
h1 {{ font-size: 28px; font-weight:700; line-height:1.25; }}
h2 {{ font-size: 20px; font-weight:700; }}
h3 {{ font-size: 16px; font-weight:600; }}
p, li, label, span, div {{ color: var(--ink-300); }}
a {{ color: var(--teal); }}
code {{
  font-family:{FONT_MONO}; font-size:11.5px;
  background: var(--navy-950); border:1px solid var(--navy-700);
  color: var(--ink-300); padding:2px 6px; border-radius:5px;
}}

/* ---- brand lockup ---------------------------------------------------- */
.tc-brand {{ display:flex; align-items:center; gap:12px; margin: 0 0 26px 4px; }}
.tc-mark {{ width:34px; height:34px; flex-shrink:0; }}
.tc-word {{
  font-family:{FONT_DISPLAY} !important; font-weight:800; font-size:15px;
  letter-spacing:.04em; line-height:1.15; color:var(--ink-100); text-transform:uppercase;
}}
.tc-word span {{
  display:block; font-weight:600; font-size:10px; letter-spacing:.22em;
  color:var(--ink-500); margin-top:2px;
}}

/* ---- card ------------------------------------------------------------ */
.tc-card {{
  background: linear-gradient(180deg, var(--navy-900) 0%, var(--navy-800) 100%);
  border:1px solid var(--navy-700); border-radius:20px;
  padding: 30px 34px; margin-bottom: 20px; position:relative; overflow:hidden;
  box-shadow: 0 30px 60px -20px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.03);
}}
/* The tri-colour rule: the brand's signature, reused on the PDF header. */
.tc-card::after {{
  content:""; position:absolute; top:0; left:34px; right:34px; height:2px;
  background: linear-gradient(90deg, var(--coral), var(--amber), var(--teal));
  border-radius:2px;
}}

.tc-eyebrow {{
  display:flex; align-items:center; gap:8px;
  font-family:{FONT_MONO} !important; font-size:11px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--teal); margin-bottom:14px;
}}
.tc-eyebrow::before {{
  content:""; width:6px; height:6px; border-radius:50%; background:var(--teal);
  box-shadow:0 0 0 3px rgba(53,190,187,.18);
}}
.tc-h1 {{ font-family:{FONT_DISPLAY} !important; font-size:28px !important;
          font-weight:700; line-height:1.25; margin:0 0 12px; color:var(--ink-100); }}
.tc-h1 .arrow {{ color:var(--ink-500); font-weight:400; margin:0 4px; }}
.tc-h1 .to {{
  background: linear-gradient(90deg, var(--coral-soft), var(--amber));
  -webkit-background-clip:text; background-clip:text; color:transparent;
}}
.tc-lede {{ color:var(--ink-300); font-size:15px; line-height:1.6;
            margin:0 0 8px; max-width:52ch; }}

/* ---- file uploader, restyled as the dropzone ------------------------- */
[data-testid="stFileUploaderDropzone"] {{
  background: rgba(255,255,255,.015);
  border:1.5px dashed var(--navy-700); border-radius:14px;
  padding: 26px 20px;
  transition: border-color .18s ease, background .18s ease;
}}
[data-testid="stFileUploaderDropzone"]:hover {{
  border-color: var(--teal); background: rgba(53,190,187,.05);
}}
[data-testid="stFileUploaderDropzone"] * {{ color: var(--ink-300) !important; }}
[data-testid="stFileUploaderDropzone"] small {{
  font-family:{FONT_MONO}; color: var(--ink-500) !important;
}}
[data-testid="stFileUploaderDropzone"] button {{
  background: transparent !important; color: var(--ink-100) !important;
  border:1px solid var(--navy-700) !important; border-radius:8px !important;
  font-family:{FONT_BODY} !important; font-weight:500 !important;
}}
[data-testid="stFileUploaderDropzone"] button:hover {{
  border-color: var(--teal) !important; color: var(--teal) !important;
}}
[data-testid="stFileUploaderFile"] {{ color: var(--ink-300); }}

/* ---- buttons --------------------------------------------------------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button,
[data-testid^="stBaseButton"] {{
  width:100%; border:1px solid var(--navy-700); border-radius:12px;
  background: rgba(255,255,255,.02); color: var(--ink-100);
  font-family:{FONT_BODY}; font-weight:600; font-size:14px; padding:12px 18px;
  transition: border-color .15s ease, background .15s ease, transform .15s ease;
}}
.stButton > button:hover, .stDownloadButton > button:hover,
.stFormSubmitButton > button:hover, [data-testid^="stBaseButton"]:hover {{
  border-color: var(--teal); color: var(--teal); background: rgba(53,190,187,.06);
}}
/* Primary = the CTA gradient from the design. */
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"],
.stFormSubmitButton > button[kind="primary"],
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {{
  border:none;
  background: linear-gradient(90deg, var(--coral) 0%, var(--coral-soft) 45%, var(--amber) 100%);
  color:{b['cta_ink']}; font-family:{FONT_DISPLAY}; font-weight:700; font-size:15px;
  padding:16px 20px; box-shadow: 0 10px 24px -10px rgba(238,90,78,.45);
}}
.stButton > button[kind="primary"]:hover,
.stDownloadButton > button[kind="primary"]:hover,
.stFormSubmitButton > button[kind="primary"]:hover,
[data-testid="stBaseButton-primary"]:hover {{
  filter: brightness(1.06); transform: translateY(-1px); color:{b['cta_ink']};
}}
.stButton > button:disabled, .stDownloadButton > button:disabled,
[data-testid^="stBaseButton"]:disabled {{
  opacity:.45; filter:none; transform:none;
}}
/* The label lives in a nested div; without this it keeps Streamlit's colour. */
[data-testid^="stBaseButton"] p, [data-testid^="stBaseButton"] div {{
  color: inherit !important; font-family: inherit !important;
  font-weight: inherit !important; font-size: inherit !important;
}}

/* ---- inputs ---------------------------------------------------------- */
.stTextInput input, .stTextArea textarea, .stNumberInput input,
[data-baseweb="select"] > div {{
  background: var(--navy-950) !important;
  border:1px solid var(--navy-700) !important;
  color: var(--ink-100) !important; border-radius:10px !important;
}}
.stTextInput input:focus, .stTextArea textarea:focus {{
  border-color: var(--teal) !important; box-shadow:0 0 0 3px rgba(53,190,187,.15) !important;
}}
.stTextInput label, .stTextArea label, .stCheckbox label, .stSlider label,
.stSelectSlider label, .stFileUploader label {{
  color: var(--ink-300) !important; font-size:13px !important; font-weight:500 !important;
}}
[data-testid="stWidgetLabel"] p {{ color: var(--ink-300) !important; font-size:13px; }}

/* ---- sidebar --------------------------------------------------------- */
[data-testid="stSidebar"] {{
  background: var(--navy-900); border-right:1px solid var(--navy-700);
}}
[data-testid="stSidebar"] * {{ color: var(--ink-300); }}
[data-testid="stSidebar"] h3, [data-testid="stSidebar"] h5 {{
  font-family:{FONT_DISPLAY}; color: var(--ink-100);
}}

/* ---- metrics --------------------------------------------------------- */
[data-testid="stMetric"] {{
  background: rgba(255,255,255,.02); border:1px solid var(--navy-700);
  border-radius:12px; padding:14px 16px;
}}
[data-testid="stMetricValue"] {{
  font-family:{FONT_DISPLAY}; font-weight:700; color: var(--ink-100);
}}
[data-testid="stMetricLabel"] p {{
  font-family:{FONT_MONO}; font-size:10px !important; letter-spacing:.1em;
  text-transform:uppercase; color: var(--ink-500) !important;
}}

/* ---- feedback blocks ------------------------------------------------- */
[data-testid="stAlert"] {{ border-radius:12px; border:1px solid var(--navy-700); }}
.stProgress > div > div > div > div {{
  background: linear-gradient(90deg, var(--coral), var(--amber), var(--teal));
}}
[data-testid="stExpander"] {{
  border:1px solid var(--navy-700); border-radius:12px; background: rgba(255,255,255,.015);
}}
[data-testid="stExpander"] summary {{ color: var(--ink-300); font-size:13px; }}
hr, [data-testid="stDivider"] {{ border-color: var(--navy-700); }}

/* ---- dataframe ------------------------------------------------------- */
[data-testid="stDataFrame"] {{ border:1px solid var(--navy-700); border-radius:12px; }}

/* ---- bespoke pieces -------------------------------------------------- */
.tc-disclosure {{
  margin-top:18px; padding-top:16px; border-top:1px solid var(--navy-700);
  font-size:12px; line-height:1.6; color:var(--ink-500);
}}
.tc-footer {{
  text-align:center; margin-top:26px; font-family:{FONT_MONO} !important; font-size:11px;
  letter-spacing:.08em; color:var(--ink-600); text-transform:uppercase;
}}
.tc-steps {{
  display:flex; gap:8px; align-items:center; margin: 0 0 22px 2px;
  font-family:{FONT_MONO} !important; font-size:10.5px; letter-spacing:.12em; text-transform:uppercase;
}}
.tc-step {{ color:var(--ink-600); display:flex; align-items:center; gap:7px; }}
.tc-step .dot {{
  width:7px; height:7px; border-radius:50%; background:var(--navy-700); flex-shrink:0;
}}
.tc-step.done {{ color:var(--ink-500); }}
.tc-step.done .dot {{ background:var(--teal); }}
.tc-step.active {{ color:var(--ink-100); }}
.tc-step.active .dot {{
  background:var(--amber); box-shadow:0 0 0 3px rgba(243,162,42,.2);
}}
.tc-step-sep {{ flex:1; height:1px; background:var(--navy-700); min-width:14px; }}

.tc-score {{ display:flex; align-items:baseline; gap:10px; }}
.tc-score .num {{ font-family:{FONT_DISPLAY} !important; font-size:52px; font-weight:800;
                  line-height:1; }}
.tc-score .den {{ font-size:15px; color:var(--ink-500); }}
.tc-score .band {{ font-family:{FONT_DISPLAY}; font-size:14px; font-weight:600; color:var(--ink-100); }}
.tc-conf {{ font-family:{FONT_MONO}; font-size:11px; color:var(--ink-500); margin-top:4px; }}

.tc-quote {{
  background: rgba(53,190,187,.06); border-left:3px solid var(--teal);
  padding:14px 16px; border-radius:0 10px 10px 0; margin-top:6px;
}}
.tc-quote .lbl {{
  font-family:{FONT_MONO}; font-size:9.5px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--teal);
}}
.tc-quote .txt {{ font-size:14px; font-style:italic; color:var(--ink-100);
                  line-height:1.55; margin-top:6px; }}

.tc-chip {{
  display:inline-block; font-family:{FONT_MONO}; font-size:9.5px; letter-spacing:.1em;
  text-transform:uppercase; padding:3px 8px; border-radius:20px;
  border:1px solid var(--navy-700); color:var(--ink-500);
}}
</style>
"""


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------


def brand_lockup() -> str:
    """The mark plus the wordmark. Uses the real logo asset when present."""
    from ui.assets import mark_data_uri

    uri = mark_data_uri(72)
    mark = (
        f'<img class="tc-mark" src="{uri}" alt="TEN Capital Network">'
        if uri
        else LOGO_FALLBACK_SVG
    )
    return (
        f'<div class="tc-brand">{mark}'
        f'<div class="tc-word">Ten Capital<span>Network</span></div></div>'
    )


def hero(eyebrow: str, title_html: str, lede: str) -> str:
    return (
        f'<div class="tc-eyebrow">{eyebrow}</div>'
        f'<div class="tc-h1">{title_html}</div>'
        f'<p class="tc-lede">{lede}</p>'
    )


def steps(current: str, names: Optional[List[str]] = None) -> str:
    """The three-stage progress rail.

    The mockup is a single upload screen; the real workflow has three stages,
    and a user who has waited ten minutes for an analysis needs to know where
    they are in it.
    """
    names = names or ["Upload", "Review competitors", "Report"]
    order = ["input", "review", "report"]
    index = order.index(current) if current in order else 0

    parts: List[str] = []
    for i, name in enumerate(names):
        state = "active" if i == index else ("done" if i < index else "")
        parts.append(f'<div class="tc-step {state}"><span class="dot"></span>{name}</div>')
        if i < len(names) - 1:
            parts.append('<div class="tc-step-sep"></div>')
    return f'<div class="tc-steps">{"".join(parts)}</div>'


def score_block(total: int, band: str, confidence: float) -> str:
    colour = score_colour(total)
    return (
        f'<div class="tc-score">'
        f'<span class="num" style="color:{colour};">{total}</span>'
        f'<span class="den">/100</span>'
        f'<span class="band">{band}</span></div>'
        f'<div class="tc-conf">Confidence {confidence:.2f}</div>'
    )


def positioning_block(text: str) -> str:
    return (
        f'<div class="tc-quote"><div class="lbl">Recommended positioning</div>'
        f'<div class="txt">{text}</div></div>'
    )


def disclosure(recipients: List[str], configured: bool) -> str:
    """Say what actually happens to the file. Driven by real settings."""
    if not configured:
        return (
            '<div class="tc-disclosure">The uploaded file is processed on the server. '
            'Email delivery is not configured, so no copy is sent.</div>'
        )
    who = ", ".join(f"<code>{r}</code>" for r in recipients)
    return (
        f'<div class="tc-disclosure">The uploaded file is processed on the server and a '
        f'copy of every generated one-pager is emailed to {who}.</div>'
    )


def footer() -> str:
    return '<div class="tc-footer">Powered by TEN Capital Network</div>'
