#!/usr/bin/env python3
"""Convert a raster logo to Superchain-compatible logo.svg (embedded PNG, 256x256)."""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "tokenlists/pr-packages/superchain/data/SINC/logo.jpg"
OUT_DIRS = [
    ROOT / "tokenlists/pr-packages/superchain/data/SINC",
    ROOT / "tokenlists/_staging/ethereum-optimism.github.io/data/SINC",
]


def jpg_to_svg(src: Path, dest: Path) -> None:
    img = Image.open(src).convert("RGBA")
    img = img.resize((256, 256), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        'viewBox="0 0 256 256" width="256" height="256">\n'
        f'  <image width="256" height="256" xlink:href="data:image/png;base64,{b64}"/>\n'
        "</svg>\n"
    )
    dest.write_text(svg, encoding="utf-8")


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    if not src.exists():
        print(f"Missing source image: {src}", file=sys.stderr)
        return 1
    for d in OUT_DIRS:
        d.mkdir(parents=True, exist_ok=True)
        out = d / "logo.svg"
        jpg_to_svg(src, out)
        print(f"Wrote {out} ({out.stat().st_size:,} bytes)")
    print("Upload logo.svg only — do not include logo.jpg in the PR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())