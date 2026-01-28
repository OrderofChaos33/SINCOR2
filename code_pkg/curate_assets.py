# curate_assets.py  picks best hero + logo from big libraries; writes to assets\hero.jpg, assets\logo.png
import re, json, hashlib
from pathlib import Path
from PIL import Image, ImageOps

ROOT   = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
# Primary sources to scan  add more if needed
SOURCES = [
    ASSETS,
    Path(r"C:\Users\cjay4\OneDrive\Desktop\SINCOR\assets"),
    Path(r"C:\Users\cjay4\OneDrive\Desktop\SINCOR\clinton_auto_detailing"),
    Path(r"C:\Users\cjay4\OneDrive\Desktop\SINCOR\shots"),
    Path(r"C:\Users\cjay4\OneDrive\Desktop\SINCOR\media_packs"),
    Path(r"C:\Users\cjay4\OneDrive\Desktop\SINCOR\out"),
]

EXTS = {".jpg",".jpeg",".png",".webp",".bmp",".tif",".tiff"}

def is_img(p: Path) -> bool:
    return p.suffix.lower() in EXTS

def score_logo(p: Path, im: Image.Image) -> float:
    w,h = im.size
    area = w*h
    alpha = (im.mode in ("LA","RGBA","PA"))
    name = p.name.lower()
    s = 0.0
    # filename signals
    if "logo" in name:         s += 6
    if "brand" in name:        s += 2
    if "transparent" in name:  s += 1
    # shape (logos are often wide-ish but not ultra-wide photos)
    ratio = w/(h or 1)
    if 1.2 <= ratio <= 4.0:    s += 2
    # transparency wins
    if alpha:                  s += 4
    # reasonable size (avoid tiny favicons)
    if max(w,h) >= 512:        s += 2
    # bigger is better, lightly normalized
    s += min(area/1_000_000, 6)
    return s

def score_hero(p: Path, im: Image.Image) -> float:
    w,h = im.size
    area = w*h
    name = p.name.lower()
    s = 0.0
    # hero cues
    if "hero" in name:         s += 5
    if "banner" in name:       s += 4
    if "after" in name:        s += 2
    if "detail" in name:       s += 1
    # landscape, big, high area
    ratio = w/(h or 1)
    if ratio >= 1.4:           s += 3
    if w >= 1600:              s += 3
    s += min(area/2_000_000, 7)
    return s

def iter_images():
    seen = set()
    for base in SOURCES:
        if not base.exists(): continue
        for p in base.rglob("*"):
            if p.is_file() and is_img(p):
                # de-dupe by hash of path string (fast); real byte hash would be pricier
                k = str(p.resolve()).lower()
                if k in seen: continue
                seen.add(k)
                yield p

def best_images():
    best_logo = (None, -1.0)
    best_hero = (None, -1.0)
    for p in iter_images():
        try:
            with Image.open(p) as im:
                im.load()
                # logo
                ls = score_logo(p, im)
                if ls > best_logo[1]:
                    best_logo = (p, ls)
                # hero
                hs = score_hero(p, im)
                if hs > best_hero[1]:
                    best_hero = (p, hs)
        except Exception:
            continue
    return best_logo[0], best_hero[0]

def export_logo(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = im.convert("RGBA")
        # fit height  120px, preserve aspect
        target_h = 120
        w,h = im.size
        if h > target_h:
            im = im.resize((int(w*target_h/h), target_h), Image.LANCZOS)
        im.save(dst)

def export_hero(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = im.convert("RGB")
        # smart contain into 1600x900 (no crop)
        im = ImageOps.contain(im, (1600, 900), Image.LANCZOS)
        im.save(dst, quality=92)

def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    logo_src, hero_src = best_images()
    if not logo_src and not hero_src:
        print("[curate_assets] no images found in sources"); return
    if logo_src:
        export_logo(logo_src, ASSETS / "logo.png")
        print(f"[curate_assets] logo -> {ASSETS / 'logo.png'}  (from {logo_src.name})")
    else:
        print("[curate_assets] no good logo candidate")
    if hero_src:
        export_hero(hero_src, ASSETS / "hero.jpg")
        print(f"[curate_assets] hero -> {ASSETS / 'hero.jpg'}  (from {hero_src.name})")
    else:
        print("[curate_assets] no good hero candidate")

if __name__ == "__main__":
    main()
