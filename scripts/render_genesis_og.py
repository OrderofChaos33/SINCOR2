#!/usr/bin/env python3
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "static" / "genesis-og.png"

def main() -> None:
    w, h = 1200, 630
    im = Image.new("RGB", (w, h), "#0b0c0a")
    d = ImageDraw.Draw(im)
    d.rectangle([36, 36, w - 36, h - 36], outline="#c4b48a", width=2)
    d.rectangle([48, 48, w - 48, 118], fill="#16140f")
    try:
        font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_md = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 16)
    except Exception:
        font_lg = font_md = font_sm = ImageFont.load_default()
    d.text((72, 68), "SINCOR  ·  FORM GEN-001", font=font_sm, fill="#c4b48a")
    d.text((72, 150), "SINCOR GENESIS", font=font_lg, fill="#f2ead4")
    d.text((72, 210), "The Agent Economy, Licensed", font=font_md, fill="#c4b48a")
    y = 290
    for row in ["CLASS          AGENT LICENSE  000001–001000", "CHAIN          BASE  8453", "SETTLEMENT     x402  /  AXM", "STATUS         PRIORITY TRANCHE OPEN"]:
        d.text((72, y), row, font=font_sm, fill="#d7d0bc")
        y += 36
    d.rectangle([w - 280, h - 140, w - 72, h - 72], outline="#c4b48a", width=2)
    d.text((w - 262, h - 118), "VERIFIED ON-CHAIN", font=font_sm, fill="#c4b48a")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    im.save(OUT, "PNG", optimize=True)
    print(f"{OUT} {im.size[0]} x {im.size[1]}")

if __name__ == "__main__":
    main()
