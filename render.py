"""Turns a content spec into carousel images, story image or reel video (Pillow + ffmpeg + edge-tts)."""
import asyncio, glob, os, random, subprocess
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

try:
    import arabic_reshaper
    try:
        from bidi import get_display
    except ImportError:
        from bidi.algorithm import get_display

    def shape(t):
        return get_display(arabic_reshaper.reshape(t))
except ImportError:  # only for local layout tests; production installs requirements.txt
    def shape(t):
        return t

from common import p

GOLD = (255, 214, 102)
WHITE = (255, 255, 255)
PALETTES = [((30, 41, 59), (190, 80, 60)), ((17, 94, 89), (250, 190, 90)),
            ((76, 29, 149), (236, 72, 153)), ((15, 23, 42), (56, 189, 248)),
            ((120, 53, 15), (253, 186, 116))]
_font_path = None


def _find_font():
    global _font_path
    if _font_path:
        return _font_path
    cands = sorted(glob.glob(p("assets", "fonts", "*.ttf")) + glob.glob(p("assets", "fonts", "*.otf")))
    cands += glob.glob("/usr/share/fonts/**/Vazirmatn*Bold*.ttf", recursive=True)
    cands += glob.glob("/usr/share/fonts/**/NotoNaskhArabic-Bold.ttf", recursive=True)
    cands += glob.glob("/usr/share/fonts/**/NotoSansArabic-Bold*.ttf", recursive=True)
    cands += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    for c in cands:
        try:
            ImageFont.truetype(c, 20)
            _font_path = c
            return c
        except Exception:
            continue
    raise RuntimeError("no usable font found")


def font(size):
    return ImageFont.truetype(_find_font(), size)


def wrap(d, text, f, maxw):
    lines, cur = [], []
    for w in text.split():
        trial = " ".join(cur + [w])
        if cur and d.textlength(shape(trial), font=f) > maxw:
            lines.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(" ".join(cur))
    return lines


def overlay(size, title, body, footer, title_color=GOLD):
    W, H = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    maxw = int(W * 0.80)
    scale = 1.0
    while True:
        tf, bf = font(int(W * 0.085 * scale)), font(int(W * 0.058 * scale))
        tl = wrap(d, title, tf, maxw) if title else []
        bl = wrap(d, body, bf, maxw) if body else []
        th, bh = int(tf.size * 1.5) * len(tl), int(bf.size * 1.55) * len(bl)
        gap = int(W * 0.04) if tl and bl else 0
        total = th + bh + gap
        if total <= H * 0.60 or scale < 0.45:
            break
        scale -= 0.07
    if total:
        pad = int(W * 0.055)
        top = (H - total) // 2
        d.rounded_rectangle((W // 2 - maxw // 2 - pad, top - pad, W // 2 + maxw // 2 + pad, top + total + pad),
                            radius=int(W * 0.045), fill=(0, 0, 0, 150))
        y = top
        for line in tl:
            d.text((W // 2, y), shape(line), font=tf, fill=title_color, anchor="ma")
            y += int(tf.size * 1.5)
        y += gap
        for line in bl:
            d.text((W // 2, y), shape(line), font=bf, fill=WHITE, anchor="ma")
            y += int(bf.size * 1.55)
    if footer:
        d.text((W // 2, int(H * 0.93)), shape(footer), font=font(int(W * 0.04)), fill=(235, 235, 235, 230), anchor="ma")
    return img


def background(path, size, seed=0):
    W, H = size
    if path and os.path.exists(path):
        try:
            im = Image.open(path).convert("RGB")
            r = max(W / im.width, H / im.height)
            im = im.resize((int(im.width * r) + 1, int(im.height * r) + 1), Image.LANCZOS)
            l, t = (im.width - W) // 2, (im.height - H) // 2
            return ImageEnhance.Brightness(im.crop((l, t, l + W, t + H))).enhance(0.62)
        except Exception as e:
            print("bad background image:", e)
    c1, c2 = PALETTES[seed % len(PALETTES)]
    col = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / (H - 1)
        col.putpixel((0, y), tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)))
    return col.resize((W, H))


def _digits(n, cfg):
    s = str(n)
    return s.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")) if cfg.get("language_code") == "fa" else s


def _save(bg, ov, path):
    bg.paste(ov, (0, 0), ov)
    bg.convert("RGB").save(path, "JPEG", quality=92)
    return path


def carousel(spec, bg_path, outdir, cfg, tag):
    size, brand, files = (1080, 1350), cfg.get("brand_handle", ""), []
    seed = hash(tag) % 100
    slides = [(spec["hook"], cfg["strings"]["swipe"])]
    slides += [(_digits(i, cfg), pt) for i, pt in enumerate(spec["points"], 1)]
    slides += [(spec.get("cta") or cfg["strings"]["default_cta"], "")]
    for n, (t, b) in enumerate(slides, 1):
        files.append(_save(background(bg_path, size, seed), overlay(size, t, b, brand), os.path.join(outdir, f"{tag}_{n}.jpg")))
    return files


def story(spec, bg_path, outdir, cfg, tag):
    size = (1080, 1920)
    ov = overlay(size, spec["hook"], spec["points"][0], cfg.get("brand_handle", ""))
    return [_save(background(bg_path, size, hash(tag) % 100), ov, os.path.join(outdir, f"{tag}.jpg"))]


# ---------- reel ----------
def _dur(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    return float(out)


def _tts(text, voice, path):
    try:
        import edge_tts

        async def go():
            await edge_tts.Communicate(text, voice).save(path)
        asyncio.run(go())
        return os.path.exists(path) and os.path.getsize(path) > 1000
    except Exception as e:
        print("tts failed:", e)
        return False


def reel(spec, bg_path, outdir, cfg, tag):
    W, H = 1080, 1920
    segs = [(spec["hook"], "")] + [("", x) for x in spec["points"]] + [(spec.get("cta") or cfg["strings"]["default_cta"], "")]
    work = os.path.join(outdir, f"{tag}_work")
    os.makedirs(work, exist_ok=True)
    pngs, voices, durs = [], [], []
    for i, (t, b) in enumerate(segs):
        txt = t or b
        v = os.path.join(work, f"v{i}.mp3")
        ok = _tts(txt, cfg.get("tts_voice", "fa-IR-FaridNeural"), v)
        if ok:
            voices.append(v)
            durs.append(_dur(v) + 0.45)
        else:
            durs.append(max(2.5, len(txt.split()) * 0.42 + 0.8))
        pngs_path = os.path.join(work, f"o{i}.png")
        overlay((W, H), t, b, cfg.get("brand_handle", "")).save(pngs_path)
        pngs.append(pngs_path)
    if len(voices) != len(segs):  # partial voice would desync, so go silent
        voices = []
    total = min(sum(durs), 85.0)

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    is_img = (not bg_path) or bg_path.lower().endswith((".jpg", ".jpeg", ".png"))
    if not bg_path:
        bg_path = os.path.join(work, "bg.jpg")
        background(None, (W, H), hash(tag) % 100).save(bg_path, quality=90)
    cmd += (["-loop", "1", "-framerate", "30"] if is_img else ["-stream_loop", "-1"]) + ["-i", bg_path]
    for pg in pngs:
        cmd += ["-i", pg]
    idx = 1 + len(pngs)
    v0 = idx
    if voices:
        for v in voices:
            cmd += ["-i", v]
        idx += len(voices)
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        idx += 1
    music = sorted(glob.glob(p("assets", "music", "*.mp3")))
    m_idx = None
    if music:
        cmd += ["-stream_loop", "-1", "-i", random.choice(music)]
        m_idx = idx

    f = [f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps=30,"
         f"eq=brightness=-0.12[b0]"]
    t = 0.0
    for i, d_ in enumerate(durs):
        f.append(f"[b{i}][{1 + i}:v]overlay=0:0:enable='between(t,{t:.2f},{t + d_:.2f})'[b{i + 1}]")
        t += d_
    if voices:
        for i, d_ in enumerate(durs):
            f.append(f"[{v0 + i}:a]apad=whole_dur={d_:.2f}[a{i}]")
        f.append("".join(f"[a{i}]" for i in range(len(durs))) + f"concat=n={len(durs)}:v=0:a=1[va]")
    else:
        f.append(f"[{v0}:a]atrim=0:{total:.2f}[va]")
    if m_idx is not None:
        f.append(f"[{m_idx}:a]volume=0.10,atrim=0:{total:.2f}[mu]")
        f.append("[va][mu]amix=inputs=2:duration=first:dropout_transition=0[aout]")
    else:
        f.append("[va]anull[aout]")
    out = os.path.join(outdir, f"{tag}.mp4")
    cmd += ["-filter_complex", ";".join(f), "-map", f"[b{len(durs)}]", "-map", "[aout]", "-t", f"{total:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    return [out]
