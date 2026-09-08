"""
Brand assets: the TEN Capital mark and the favicon set.

ARCHITECTURAL DECISION -- page_icon for the tab, data URI for the lockup
------------------------------------------------------------------------
The tab icon is set through `st.set_page_config(page_icon=...)`. That is not a
stylistic preference: Streamlit sanitises `<link>` tags out of both `st.html`
and `st.markdown`, so hand-injecting `<link rel="icon">` produces markup that
is silently discarded -- verified in a real browser, where zero injected link
tags survived. `page_icon` is the only mechanism that actually sets the icon,
and Streamlit serves the image itself.

The header lockup is a base64 data URI rather than a URL, because Streamlit
serves static files under a prefix that differs between a local run and a
proxied deployment, and a data URI cannot 404.

The generated PNG/ICO files live in `static/`, which is the project's public
directory -- served at `/app/static/<file>` with `enableStaticServing`. They
are the canonical assets for anything outside the Streamlit page: a web
manifest, an email template, or a direct link.
"""

from __future__ import annotations

import base64
import functools
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSET_DIR = PROJECT_ROOT / "assets"
STATIC_DIR = PROJECT_ROOT / "static"

#: The square, padding-normalised master. Regenerate the set with
#: `python -m ui.assets` if this is ever replaced.
MASTER = ASSET_DIR / "ten_capital_mark.png"

FAVICON_ICO = STATIC_DIR / "favicon.ico"
FAVICON_32 = STATIC_DIR / "favicon-32.png"
APPLE_TOUCH = STATIC_DIR / "apple-touch-icon.png"

#: Every generated size, as (filename, pixels). `favicon.ico` is multi-resolution.
ICON_SIZES = [
    ("favicon-16.png", 16),
    ("favicon-32.png", 32),
    ("favicon-48.png", 48),
    ("apple-touch-icon.png", 180),
    ("icon-192.png", 192),
    ("icon-512.png", 512),
]


@functools.lru_cache(maxsize=8)
def data_uri(path: str) -> Optional[str]:
    """Base64 data URI for an asset, or None when it is missing.

    Missing assets degrade to no icon rather than to a broken image: the app
    is an analysis tool, and it must not fail to start over decoration.
    """
    file = Path(path)
    if not file.exists():
        return None
    mime = "image/x-icon" if file.suffix == ".ico" else "image/png"
    return f"data:{mime};base64,{base64.b64encode(file.read_bytes()).decode('ascii')}"


@functools.lru_cache(maxsize=4)
def mark_data_uri(size: int = 72) -> Optional[str]:
    """The mark, downscaled for inline use in the header lockup."""
    if not MASTER.exists():
        return None
    try:
        import io

        from PIL import Image

        image = Image.open(MASTER).convert("RGBA").resize((size, size), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"
    except Exception:
        return None


#: Where Streamlit serves ./static from when static serving is enabled.
STATIC_URL_PREFIX = "/app/static"


def static_url(filename: str) -> str:
    """Public URL for a generated asset, e.g. for a manifest or an email."""
    return f"{STATIC_URL_PREFIX}/{filename}"


def page_icon():
    """Value for `st.set_page_config(page_icon=...)`.

    Streamlit accepts a PIL image, which it renders as the tab icon itself.
    Falling back to an emoji keeps the app starting if the asset is absent.
    """
    if not FAVICON_32.exists():
        return "◈"
    try:
        from PIL import Image

        return Image.open(FAVICON_32)
    except Exception:
        return "◈"


def regenerate(source: Optional[Path] = None) -> list[Path]:
    """Rebuild the favicon set from a source image.

    Kept in the codebase rather than done once by hand so the asset pipeline is
    reproducible when the brand mark is updated.
    """
    from PIL import Image

    source = Path(source) if source else MASTER
    image = Image.open(source).convert("RGBA")

    # Trim uniform transparent padding: a 16px favicon cannot afford margin.
    bbox = image.split()[-1].getbbox()
    if bbox:
        image = image.crop(bbox)
    side = max(image.size)
    pad = int(side * 0.04)
    canvas = Image.new("RGBA", (side + 2 * pad, side + 2 * pad), (0, 0, 0, 0))
    canvas.paste(image, ((canvas.width - image.width) // 2, (canvas.height - image.height) // 2))

    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    canvas.save(MASTER)

    written = [MASTER]
    for name, size in ICON_SIZES:
        target = STATIC_DIR / name
        canvas.resize((size, size), Image.LANCZOS).save(target, optimize=True)
        written.append(target)

    canvas.resize((256, 256), Image.LANCZOS).save(
        FAVICON_ICO,
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    written.append(FAVICON_ICO)
    return written


if __name__ == "__main__":  # pragma: no cover
    import sys

    for path in regenerate(Path(sys.argv[1]) if len(sys.argv) > 1 else None):
        print(f"wrote {path.relative_to(PROJECT_ROOT)}")
