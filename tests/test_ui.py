"""
Brand theme and interface chrome.

The interesting failures here are the ones a screenshot would not catch: an
interface that advertises a file format the pipeline cannot read, a hardcoded
email address that outlives a config change, or a palette tweak that quietly
drops text below the contrast floor.
"""

from __future__ import annotations

import re

from config import Settings
from ingestion.normalizer import SUPPORTED_DECK_SUFFIXES
from ui import theme


# --------------------------------------------------------------------------
# Accessibility
# --------------------------------------------------------------------------


def _relative_luminance(hex_colour: str) -> float:
    hex_colour = hex_colour.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        c = int(hex_colour[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def test_body_text_meets_wcag_aa():
    """Normal-size text must clear 4.5:1 against the surface it sits on."""
    b = theme.BRAND
    for name, fg, bg in [
        ("ink_100 on card", b["ink_100"], b["navy_900"]),
        ("ink_300 on card", b["ink_300"], b["navy_900"]),
        ("ink_500 on card", b["ink_500"], b["navy_900"]),
        ("ink_600 on page", b["ink_600"], b["navy_950"]),
        ("teal on card", b["teal"], b["navy_900"]),
        ("amber on card", b["amber"], b["navy_900"]),
        ("coral on card", b["coral"], b["navy_900"]),
    ]:
        ratio = contrast(fg, bg)
        assert ratio >= 4.5, f"{name} is {ratio:.2f}:1, below the AA floor of 4.5"


def test_cta_label_is_readable_on_the_gradient():
    """The CTA text sits on coral at the darkest end of the gradient."""
    assert contrast(theme.BRAND["cta_ink"], theme.BRAND["coral"]) >= 4.5


def test_score_colours_are_readable():
    for total in (10, 55, 90):
        assert contrast(theme.score_colour(total), theme.BRAND["navy_900"]) >= 4.5


# --------------------------------------------------------------------------
# The stylesheet
# --------------------------------------------------------------------------


def test_stylesheet_has_no_unresolved_placeholders():
    """An f-string brace mistake yields CSS that silently does nothing."""
    css = theme.stylesheet()
    assert "{{" not in css and "}}" not in css
    assert not re.search(r"\{[a-z_]+\}", css), "unsubstituted placeholder in the CSS"


def test_stylesheet_carries_the_brand_tokens():
    css = theme.stylesheet()
    for key in ("navy_950", "coral", "amber", "teal", "ink_100"):
        assert theme.BRAND[key] in css, f"{key} missing from the stylesheet"


def test_stylesheet_is_delivered_as_a_style_block():
    """Streamlit's markdown sanitiser drops <style>; the app must use st.html."""
    assert theme.stylesheet().lstrip().startswith("<style>")
    app_source = __import__("pathlib").Path("app.py").read_text(encoding="utf-8")
    injected = [
        line for line in app_source.splitlines()
        if "theme.stylesheet()" in line and "st.html(" in line
    ]
    assert injected, "the stylesheet must go through st.html, not st.markdown"
    assert "st.markdown(theme.stylesheet()" not in app_source


def test_stylesheet_avoids_build_specific_class_names():
    """`.st-emotion-cache-*` names change between Streamlit releases."""
    assert "st-emotion-cache" not in theme.stylesheet()


def test_fonts_are_loaded_by_import_not_link_tag():
    css = theme.stylesheet()
    assert "@import url(" in css
    assert "Sora" in css and "Inter" in css and "JetBrains" in css


# --------------------------------------------------------------------------
# Chrome driven by real state, not copy
# --------------------------------------------------------------------------


def test_accepted_formats_match_what_the_pipeline_can_read():
    """The mockup advertised .docx, which this application cannot ingest."""
    app_source = __import__("pathlib").Path("app.py").read_text(encoding="utf-8")
    assert "SUPPORTED_DECK_SUFFIXES" in app_source, "format list must come from ingestion"
    assert '"docx"' not in app_source and "'docx'" not in app_source
    assert SUPPORTED_DECK_SUFFIXES == {".pdf", ".pptx"}


def test_disclosure_names_the_configured_recipient():
    """The mockup hardcoded a personal address; it must follow settings."""
    settings = Settings()
    settings.email_to = ["Info@tencapital.group"]
    html = theme.disclosure(settings.email_to, configured=True)
    assert "Info@tencapital.group" in html
    assert "gmail.com" not in html


def test_disclosure_is_honest_when_email_is_off():
    html = theme.disclosure([], configured=False)
    assert "not configured" in html
    assert "emailed to" not in html


def test_no_hardcoded_email_address_in_the_interface():
    app_source = __import__("pathlib").Path("app.py").read_text(encoding="utf-8")
    theme_source = __import__("pathlib").Path("ui/theme.py").read_text(encoding="utf-8")
    for source in (app_source, theme_source):
        assert not re.search(r"[\w.+-]+@(?!tencapital)[\w-]+\.\w+", source), (
            "an email address is hardcoded in the interface"
        )


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------


def test_steps_marks_the_current_stage():
    for stage, expected in [("input", 0), ("review", 1), ("report", 2)]:
        html = theme.steps(stage)
        actives = re.findall(r'class="tc-step ([a-z]*)"', html)
        assert actives[expected] == "active", f"{stage} not marked active"
        assert all(a == "done" for a in actives[:expected])


def test_steps_handles_an_unknown_stage():
    assert 'class="tc-step active"' in theme.steps("nonsense")


def test_score_block_uses_the_band_colour():
    assert theme.BRAND["coral"] in theme.score_block(30, "Commodity", 0.5)
    assert theme.BRAND["teal"] in theme.score_block(90, "Highly differentiated", 0.9)


def test_brand_lockup_includes_the_mark_and_wordmark():
    html = theme.brand_lockup()
    assert "tc-mark" in html and "Ten Capital" in html and "Network" in html


def test_brand_lockup_uses_the_real_logo_asset():
    html = theme.brand_lockup()
    assert "data:image/png;base64," in html, "the lockup should use the brand asset"
    assert 'alt="TEN Capital Network"' in html, "the mark needs alt text"


def test_logo_fallback_uses_the_three_brand_colours():
    """Used only when the asset is missing; it must still be on-brand."""
    for key in ("coral", "amber", "teal"):
        assert theme.BRAND[key] in theme.LOGO_FALLBACK_SVG


# --------------------------------------------------------------------------
# The PDF carries the same brand
# --------------------------------------------------------------------------


def test_pdf_accent_matches_the_interface_navy():
    from reporting.layout import ACCENT

    assert ACCENT.hexval()[2:].upper() == theme.BRAND["navy_700"].lstrip("#").upper()


def test_pdf_signature_rule_uses_the_three_brand_colours():
    from reporting.layout import BRAND_RULE

    rendered = {c.hexval()[2:].upper() for c in BRAND_RULE}
    expected = {theme.BRAND[k].lstrip("#").upper() for k in ("coral", "amber", "teal")}
    assert rendered == expected


# --------------------------------------------------------------------------
# Favicon and brand assets
# --------------------------------------------------------------------------


def test_every_generated_icon_exists_and_is_square():
    from PIL import Image

    from ui import assets

    for name, size in assets.ICON_SIZES:
        path = assets.STATIC_DIR / name
        assert path.exists(), f"missing generated icon: static/{name}"
        with Image.open(path) as image:
            assert image.size == (size, size), f"{name} is {image.size}, expected {size}px"


def test_multi_resolution_ico_exists():
    """Legacy browsers and Windows shortcuts request favicon.ico by convention."""
    from ui import assets

    assert assets.FAVICON_ICO.exists()
    assert assets.FAVICON_ICO.stat().st_size > 0


def test_icons_are_not_mostly_empty_padding():
    """A favicon with wide margins is illegible at 16px."""
    from PIL import Image

    from ui import assets

    with Image.open(assets.STATIC_DIR / "icon-512.png") as image:
        alpha = image.convert("RGBA").split()[-1]
        bbox = alpha.getbbox()
    coverage = ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / (image.width * image.height)
    assert coverage > 0.8, f"the mark fills only {coverage:.0%} of the icon"


def test_tab_icon_is_set_through_page_config():
    """Streamlit strips injected <link> tags; page_icon is the only mechanism.

    Verified in a real browser: zero hand-injected link tags survived, so a
    hand-rolled <link rel="icon"> would be markup that silently does nothing.
    """
    app_source = __import__("pathlib").Path("app.py").read_text(encoding="utf-8")
    assert "page_icon=assets.page_icon()" in app_source
    assert "favicon_links" not in app_source, "injected link tags are discarded"


def test_static_url_points_at_the_public_directory():
    from ui import assets

    assert assets.static_url("favicon.ico") == "/app/static/favicon.ico"


def test_page_icon_returns_an_image_when_the_asset_is_present():
    from ui import assets

    icon = assets.page_icon()
    assert not isinstance(icon, str), "should return the real mark, not the emoji fallback"


def test_missing_asset_degrades_instead_of_raising(monkeypatch, tmp_path):
    """The app must still start if the brand asset is absent."""
    from ui import assets

    assets.data_uri.cache_clear()
    monkeypatch.setattr(assets, "FAVICON_32", tmp_path / "absent.png")
    monkeypatch.setattr(assets, "MASTER", tmp_path / "absent.png")
    assets.mark_data_uri.cache_clear()
    assert assets.page_icon() == "◈"
    assert assets.mark_data_uri(72) is None
    assets.data_uri.cache_clear()
    assets.mark_data_uri.cache_clear()


def test_static_serving_is_enabled_for_the_public_assets():
    import tomllib

    config = tomllib.load(open(".streamlit/config.toml", "rb"))
    assert config["server"]["enableStaticServing"] is True


def test_brand_colours_match_the_logo_asset():
    """The palette is sampled from the mark; drift would show side by side."""
    from collections import Counter

    from PIL import Image

    from ui import assets

    with Image.open(assets.MASTER) as image:
        pixels = list(image.convert("RGBA").get_flattened_data())
    counts = Counter(
        (r, g, b) for r, g, b, a in pixels if a > 200 and not (r > 235 and g > 235 and b > 235)
    )
    dominant = {f"#{r:02X}{g:02X}{b:02X}" for (r, g, b), _ in counts.most_common(3)}
    for key in ("coral", "amber", "teal"):
        assert theme.BRAND[key].upper() in dominant, (
            f"BRAND['{key}'] = {theme.BRAND[key]} is not a dominant colour of the logo {dominant}"
        )
