# scripts_render_video.py
# Build a vertical (1080x1920) ad from a storyboard CSV.
# Usage:
#   pip install moviepy==1.0.3 pillow qrcode[pil] numpy
#   python scripts_render_video.py --shots shots/biz_1.csv --out out/biz_1.mp4 \
#     --url "https://clintondetailing.com/booking" --phone "1-815-718-8936"

import argparse, csv, os, random, io
from pathlib import Path

import numpy as np

from moviepy.editor import (
    VideoFileClip, ImageClip, AudioFileClip, CompositeVideoClip,
    ColorClip, concatenate_videoclips, vfx
)

from PIL import Image, ImageDraw, ImageFont
# --- Pillow 10+ compatibility: ANTIALIAS moved under Resampling ---
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

import qrcode

W, H, FPS = 1080, 1920, 30

ASSET_FOLDERS = {
    "video": ["assets/video", "assets/videos", "assets_unstructured", "assets"],
    "image": ["assets/images", "assets_unstructured", "assets"],
    "music": ["assets/music", "assets"]
}

def pick_assets(kind):
    exts = { "video": (".mp4",".mov",".mkv",".m4v"),
             "image": (".jpg",".jpeg",".png",".webp"),
             "music": (".mp3",".wav",".m4a") }[kind]
    bank = []
    for folder in ASSET_FOLDERS[kind]:
        p = Path(folder)
        if p.exists():
            bank += [str(x) for x in p.rglob("*") if x.suffix.lower() in exts]
    return bank

def fit_916(clip):
    # Scale to cover 1080x1920 and center-crop if needed
    scale = max(W / clip.w, H / clip.h)
    clip = clip.resize(scale)
    return clip.crop(x_center=clip.w/2, y_center=clip.h/2, width=W, height=H)

def _wrap_text(draw, font, text, max_width):
    lines, cur = [], ""
    for word in text.split():
        test = (cur + " " + word).strip()
        if draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            if cur: lines.append(cur)
            cur = word
    if cur: lines.append(cur)
    return lines

def txt_clip(text, fontsize=64, pos=("center","bottom"), color="white",
             stroke_color="black", stroke_width=3, duration=3):
    margin = 40
    max_w = int(W*0.86)
    try:
        font = ImageFont.truetype("arial.ttf", fontsize)
    except Exception:
        font = ImageFont.load_default()

    # measure/wrap
    dummy = Image.new("RGBA", (W, 200), (0,0,0,0))
    d0 = ImageDraw.Draw(dummy)
    lines = _wrap_text(d0, font, text, max_w)
    line_h = font.size + 6
    box_h = line_h*len(lines) + margin

    img = Image.new("RGBA", (W, box_h), (0,0,0,0))
    d = ImageDraw.Draw(img)
    y = int(margin/2)
    for l in lines:
        x = int((W - d.textlength(l, font=font))/2)
        # outline
        for dx in (-stroke_width,0,stroke_width):
            for dy in (-stroke_width,0,stroke_width):
                if dx==0 and dy==0: continue
                d.text((x+dx,y+dy), l, font=font, fill=stroke_color)
        d.text((x,y), l, font=font, fill=color)
        y += line_h

    arr = np.array(img)  # PIL → numpy
    clip = ImageClip(arr).set_duration(duration)

    # position
    if pos == ("center","bottom"):
        return clip.set_position(("center", H - clip.h - 60))
    elif pos == ("center","top"):
        return clip.set_position(("center", 60))
    else:
        return clip.set_position(pos)

def qr_clip_for(url, duration=3, width=260, pos=(W-300, H-300)):
    qr_img = qrcode.make(url)
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    arr = np.array(Image.open(io.BytesIO(buf.getvalue())).convert("RGBA"))
    return ImageClip(arr).set_duration(duration).resize(width=width).set_position(pos)

def make_endcard(url, phone, logo_path=None,
                 service_line="Interior Reset • Stain • Odor • Pet Hair",
                 duration=3, qr=True):
    bg = ColorClip(size=(W,H), color=(10,10,10)).set_duration(duration)
    layers = [bg]

    if qr and url:
        layers.append(qr_clip_for(url, duration=duration))

    if logo_path and Path(logo_path).exists():
        logo = ImageClip(str(logo_path)).set_duration(duration).resize(width=220).set_position((40,40))
        layers.append(logo)

    layers.append(txt_clip(f"{url} · {phone}", fontsize=58, duration=duration, pos=("center","center")))
    layers.append(txt_clip(service_line, fontsize=46, duration=duration, pos=("center","top")))
    return CompositeVideoClip(layers).set_duration(duration)

def load_clips_for_slot(start, end, label, overlay_text, media_pool):
    """
    Asset rules (no metadata required):
      - HOOK/BENEFIT -> prefer RANDOM VIDEO; if none, fall back to images.
      - PROOF        -> prefer RANDOM IMAGE (flyers/cool cars are fine).
      - CTA handled by END CARD separately.
    """
    dur = max(0.5, float(end - start))
    label_key = label.replace("[","").replace("]","").strip().upper()

    use_video = label_key in ("HOOK","BENEFIT") and bool(media_pool["video"])
    base = None

    if use_video:
        path = random.choice(media_pool["video"])
        vc = VideoFileClip(path, audio=False)
        # keep it simple: take from 0 to dur (or full if shorter)
        sub = vc.subclip(0, min(dur, max(0.5, getattr(vc, "duration", dur))))
        base = fit_916(sub).set_duration(dur)
    elif media_pool["image"]:
        path = random.choice(media_pool["image"])
        ic = ImageClip(path).set_duration(dur)
        base = fit_916(ic)
    else:
        base = ColorClip(size=(W,H), color=(20,20,20)).set_duration(dur)

    # text overlay position
    pos_map = {
        "HOOK": ("center","top"),
        "BENEFIT": ("center","bottom"),
        "PROOF": ("center","center"),
        "CTA": ("center","bottom"),
    }
    pos = pos_map.get(label_key, ("center","bottom"))
    fontsize = 68 if label_key=="HOOK" else 58 if label_key in ("BENEFIT","CTA") else 54

    overlay = txt_clip(overlay_text, fontsize=fontsize, pos=pos, duration=dur)
    return CompositeVideoClip([base, overlay]).set_duration(dur)

def auto_duck(bg_audio, video):
    try:
        return bg_audio.volumex(0.35)
    except Exception:
        return bg_audio

def parse_csv(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                s = int(row["start_sec"]); e = int(row["end_sec"])
                label = row["label"].strip()
                text = row["overlay_text"].strip()
                rows.append((s,e,label,text))
            except Exception:
                continue
    rows.sort(key=lambda x: x[0])
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", required=True, help="CSV from scripts_storyboard_to_csv.py")
    ap.add_argument("--out", required=True, help="Output mp4")
    ap.add_argument("--url", default="https://clintondetailing.com/booking")
    ap.add_argument("--phone", default="1-815-718-8936")
    ap.add_argument("--logo", default="assets/logo.png")
    ap.add_argument("--music", default=None, help="Optional bg music file; if omitted, will try assets/music")
    ap.add_argument("--service_line", default="Interior Reset • Stain • Odor • Pet Hair")
    args = ap.parse_args()

    rows = parse_csv(args.shots)
    if not rows:
        raise SystemExit("No rows parsed from CSV. Expect columns: start_sec,end_sec,label,overlay_text")

    pool = {
        "video": pick_assets("video"),
        "image": pick_assets("image"),
        "music": pick_assets("music"),
    }

    timeline = []
    for (s,e,label,text) in rows:
        lbl_key = label.replace("[","").replace("]","").strip().upper()
        if lbl_key == "END CARD":
            timeline.append(make_endcard(args.url, args.phone, logo_path=args.logo,
                                         service_line=args.service_line,
                                         duration=max(2, e - s), qr=True))
        else:
            timeline.append(load_clips_for_slot(s,e,label,text, pool))

    video = concatenate_videoclips(timeline, method="compose").set_fps(FPS)

    # background music (optional)
    music_file = None
    if args.music and Path(args.music).exists():
        music_file = args.music
    elif pool["music"]:
        music_file = random.choice(pool["music"])

    if music_file:
        bg = AudioFileClip(music_file)
        bg = auto_duck(bg, video)
        try:
            bg = bg.audio_loop(duration=video.duration)
        except Exception:
            bg = bg.set_duration(video.duration)
        video = video.set_audio(bg)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    video.write_videofile(args.out, fps=FPS, codec="libx264", audio_codec="aac",
                          threads=4, preset="medium", bitrate="6000k")

if __name__ == "__main__":
    main()
