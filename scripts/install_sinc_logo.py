#!/usr/bin/env python3
"""Install user-provided SINC planet logo into static + token list assets."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
# Session upload path (first install); thereafter uses static/sinc_logo_planet.jpg
_SESSION_ASSETS = ROOT.parent.parent.parent / "sessions" / (
    "C%3A%5CUsers%5Ccjay4%5C.grok%5Cworktrees%5Cdesktop-sincor-clean%5Csincor"
) / "019eae66-f5b8-7301-a275-920d4bf190cf" / "assets"
PLANET = _SESSION_ASSETS / "image-04c0bcf7-e6da-427a-8db3-753c2ef1e7c7.jpg"
OG = _SESSION_ASSETS / "image-c4c6a439-9797-441b-82ec-3170fa106b72.jpg"
if not PLANET.exists():
    PLANET = STATIC / "sinc_logo_planet.jpg"
LOGO_URL = "https://getsincor.com/static/tokenlists/assets/logo-256.png"


def square_crop(img: Image.Image) -> Image.Image:
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def main() -> int:
    if not PLANET.exists():
        print(f"Missing source logo: {PLANET}", file=sys.stderr)
        return 1

    STATIC.mkdir(exist_ok=True)
    shutil.copy2(PLANET, STATIC / "sinc_logo_planet.jpg")
    shutil.copy2(PLANET, STATIC / "sincor_logo_new.jpg")
    if OG.exists():
        shutil.copy2(OG, STATIC / "sincor_og.jpg")

    img = square_crop(Image.open(PLANET).convert("RGBA"))
    targets = [
        (256, STATIC / "tokenlists" / "assets" / "logo-256.png"),
        (256, STATIC / "tokenlists" / "assets" / "logo.png"),
        (64, STATIC / "sincor_nav_icon.png"),
        (32, STATIC / "sincor_favicon.png"),
    ]
    for size, out in targets:
        out.parent.mkdir(parents=True, exist_ok=True)
        square_crop(img).resize((size, size), Image.Resampling.LANCZOS).save(
            out, format="PNG", optimize=True
        )
        print(f"wrote {out.relative_to(ROOT)}")

    svg_path = STATIC / "tokenlists" / "assets" / "logo.svg"
    svg_path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">'
        f'<image href="{LOGO_URL}" width="256" height="256"/></svg>\n',
        encoding="utf-8",
    )
    print(f"wrote {svg_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())