"""
shotforge.engine — 3B cihaz çerçeveli, başlıklı App Store slayt render motoru.
Yalnızca Pillow + numpy kullanır; çerçeveler kodla çizilir.
"""
from __future__ import annotations

import math
import random
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
SS = 2  # süper-örnekleme çarpanı (kenar yumuşatma)

# size: App Store Connect'in beklediği tam piksel boyutu (portre)
# asc : asc-cli / fastlane klasör adı (kullandığınız yükleme aracının belgesiyle doğrulayın)
DEVICES = {
    "iphone-6.9": dict(size=(1320, 2868), kind="phone", asc="APP_IPHONE_67", label='iPhone 6.9" (17/16 Pro Max)'),
    "iphone-6.9b": dict(size=(1290, 2796), kind="phone", asc="APP_IPHONE_67", label='iPhone 6.9" (1290x2796)'),
    "iphone-6.5": dict(size=(1284, 2778), kind="phone", asc="APP_IPHONE_65", label='iPhone 6.5"'),
    "ipad-13": dict(size=(2064, 2752), kind="tablet", asc="APP_IPAD_PRO_3GEN_129", label='iPad 13"'),
    "ipad-12.9": dict(size=(2048, 2732), kind="tablet", asc="APP_IPAD_PRO_3GEN_129", label='iPad Pro 12.9"'),
}

PREVIEW_SIZES = {
    "iphone-6.9": (886, 1920),
    "iphone-6.5": (886, 1920),
    "ipad-13": (1200, 1600),  # Apple sayfasından teyit edin
}

FRAMES = {  # (açık metal, koyu metal)
    "black": ((78, 78, 84), (24, 24, 28)),
    "silver": ((238, 238, 242), (172, 172, 182)),
    "titanium": ((200, 192, 180), (120, 113, 102)),
    "blue": ((116, 132, 158), (46, 58, 80)),
    "orange": ((238, 154, 96), (164, 82, 32)),
    "pink": ((244, 190, 205), (190, 120, 140)),
}

BG_PRESETS = {
    "violet": (["#3B1D8F", "#7C3AED", "#EC4899"], 160),
    "ocean": (["#0B1B3F", "#1D4ED8", "#22D3EE"], 150),
    "sunset": (["#7F1D1D", "#F97316", "#FBBF24"], 160),
    "mint": (["#053F32", "#10B981", "#A7F3D0"], 150),
    "midnight": (["#020617", "#0F172A", "#312E81"], 160),
    "candy": (["#F0ABFC", "#A78BFA", "#60A5FA"], 150),
    "peach": (["#FFE4E6", "#FDBA74", "#FDA4AF"], 150),
    "snow": (["#FFFFFF", "#EEF2FF", "#DDE3FF"], 160),
}

# yaw > 0: ekran sağa döner. roll: düzlem içi (negatif = saat yönünün tersi)
STYLES = {
    "flat": dict(phones=[dict(yaw=0, pitch=0, roll=0, width=0.80, mode="fit")]),
    "tilt-right": dict(phones=[dict(yaw=24, pitch=-7, roll=-5, width=0.80, mode="fit")]),
    "tilt-left": dict(phones=[dict(yaw=-24, pitch=-7, roll=5, width=0.80, mode="fit")]),
    "peek": dict(phones=[dict(yaw=-14, pitch=-4, roll=4, width=1.02, mode="top", dy=0.0)]),
    "peek-flat": dict(phones=[dict(yaw=0, pitch=0, roll=0, width=0.86, mode="top", dy=0.0)]),
    "rotate-l": dict(phones=[dict(yaw=0, pitch=0, roll=-14, width=0.98, mode="top", cx=0.60, dy=0.0)]),
    "rotate-r": dict(phones=[dict(yaw=0, pitch=0, roll=14, width=0.98, mode="top", cx=0.40, dy=0.0)]),
    "duo-flat": dict(phones=[
        dict(yaw=0, pitch=0, roll=0, width=0.56, mode="fit", cx=0.31, dy=0.07, second=True),
        dict(yaw=0, pitch=0, roll=0, width=0.60, mode="fit", cx=0.68, dy=0.0),
    ]),
    "duo": dict(phones=[
        dict(yaw=24, pitch=-4, roll=-4, width=0.58, mode="fit", cx=0.32, dy=0.07, second=True),
        dict(yaw=-18, pitch=-4, roll=4, width=0.62, mode="fit", cx=0.66, dy=0.0),
    ]),
}


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def hex2rgb(s: str) -> tuple[int, int, int]:
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(len(a)))


def luminance(rgb) -> float:
    r, g, b = [c / 255 for c in rgb]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def resolve_frame(f: str):
    """'black' gibi ad ya da '#a3e635' gibi düz renk -> (açık, koyu)."""
    if f in FRAMES:
        return FRAMES[f]
    if re.fullmatch(r"#?[0-9a-fA-F]{3}([0-9a-fA-F]{3})?", f.strip()):
        c = hex2rgb(f)
        return (c, tuple(int(v * 0.86) for v in c))
    raise SystemExit(f"Bilinmeyen çerçeve rengi: {f} ({', '.join(FRAMES)} ya da #hex)")


def rr_mask(size, box, radius, ss=3) -> Image.Image:
    w, h = size
    m = Image.new("L", (w * ss, h * ss), 0)
    x0, y0, x1, y1 = [v * ss for v in box]
    ImageDraw.Draw(m).rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=radius * ss, fill=255)
    return m.resize((w, h), Image.LANCZOS)


# --------------------------------------------------------------------------- #
# Arka plan
# --------------------------------------------------------------------------- #
def parse_bg(spec: str, angle: float | None):
    if spec in BG_PRESETS:
        cols, ang = BG_PRESETS[spec]
        return [hex2rgb(c) for c in cols], (angle if angle is not None else ang), None
    p = Path(spec)
    if p.exists() and p.is_file():
        im = Image.open(p).convert("RGB")
        avg = tuple(int(v) for v in np.asarray(im.resize((16, 16))).reshape(-1, 3).mean(0))
        return [avg], 0, im
    cols = [hex2rgb(c) for c in spec.split(",") if c.strip()]
    if not cols:
        raise SystemExit(f"Geçersiz bg: {spec}")
    if len(cols) == 1:
        cols = [cols[0], cols[0]]
    return cols, (angle if angle is not None else 160), None


def make_background(size, spec, angle=None, glow=True):
    W, H = size
    cols, ang, img = parse_bg(spec, angle)
    avg_lum = float(np.mean([luminance(c) for c in cols]))
    if img is not None:
        return ImageOps.fit(img, size, Image.LANCZOS).convert("RGBA"), avg_lum
    lw, lh = max(W // 4, 64), max(H // 4, 64)
    ys, xs = np.mgrid[0:lh, 0:lw].astype(np.float32)
    xs = (xs / (lw - 1) - 0.5) * W
    ys = (ys / (lh - 1) - 0.5) * H
    a = math.radians(ang)
    dx, dy = math.sin(a), -math.cos(a)
    extent = abs(W * dx) + abs(H * dy)
    t = np.clip((xs * dx + ys * dy) / extent + 0.5, 0, 1)
    stops = np.linspace(0, 1, len(cols))
    arr = np.stack([np.interp(t, stops, [c[i] for c in cols]) for i in range(3)], -1).astype(np.float32)
    if glow:
        yy, xx = np.mgrid[0:lh, 0:lw].astype(np.float32)
        xx /= lw
        yy /= lh
        for (cx, cy, r, k) in [(0.15, 0.10, 0.55, 0.20), (0.90, 0.78, 0.50, 0.14), (0.80, 0.05, 0.35, 0.08)]:
            d2 = ((xx - cx) ** 2 + (yy - cy) ** 2) / (r * r)
            al = (k * np.exp(-d2))[..., None]
            arr = arr * (1 - al) + np.array((255, 255, 255), np.float32) * al
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").resize(size, Image.BICUBIC)
    return im.convert("RGBA"), avg_lum


def add_grain(img: Image.Image, strength=1.6, seed=7) -> Image.Image:
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    rng = np.random.default_rng(seed)
    arr += rng.normal(0, strength, arr.shape[:2])[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


# --------------------------------------------------------------------------- #
# Yazı
# --------------------------------------------------------------------------- #
def resolve_font(weight: str, user_font: str | None) -> str:
    cands = []
    if user_font:
        cands.append(Path(user_font))
    cands += [HERE / "fonts" / f"Inter-{weight}.otf", HERE / "fonts" / f"Inter-{weight}.ttf"]
    cands += [Path(p) for p in (
        "/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial Unicode.ttf", "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]
    for c in cands:
        if c.exists():
            return str(c)
    raise SystemExit("Font bulunamadı. font ile bir .ttf/.otf yolu verin.")


def parse_markup(text: str):
    """'Çok *hızlı* başla' -> paragraflar: [[(kelime, vurgu_mu), ...], ...]"""
    paras, accent = [], False
    for line in text.split("\n"):
        words = []
        for i, seg in enumerate(line.split("*")):
            if i > 0:
                accent = not accent
            for w in seg.split():
                words.append((w, accent))
        paras.append(words)
    return paras


def wrap(paras, font, max_w):
    space = font.getlength(" ")
    lines = []
    for words in paras:
        cur, cur_w = [], 0.0
        for w, acc in words:
            ww = font.getlength(w)
            add = ww if not cur else space + ww
            if cur and cur_w + add > max_w:
                lines.append((cur, cur_w))
                cur, cur_w = [(w, acc, ww)], ww
            else:
                cur.append((w, acc, ww))
                cur_w += add
        if cur:
            lines.append((cur, cur_w))
    return lines, space


class TextBlock:
    def __init__(self, title, subtitle, W, H, fp_title, fp_sub, color, accent, align="center",
                 title_scale=1.0, margin=0.075):
        self.W, self.H, self.align = W, H, align
        self.color, self.accent = color, accent
        self.margin = int(W * margin)
        max_w = W - 2 * self.margin
        self.parts = []
        if title:
            self.parts.append(self._fit(title, fp_title, int(W * 0.098 * title_scale), int(W * 0.050), max_w, 3, 1.10, color, 0))
        if subtitle:
            sub_col = (*color[:3], 205)
            self.parts.append(self._fit(subtitle, fp_sub, int(W * 0.043), int(W * 0.028), max_w, 2, 1.32,
                                        sub_col, int(W * 0.028) if title else 0, accent_ok=False))
        self.height = sum(p["gap"] + len(p["lines"]) * p["line_h"] for p in self.parts)
        self.title_h = (self.parts[0]["gap"] + len(self.parts[0]["lines"]) * self.parts[0]["line_h"]) if title else 0
        self.title_w = max((w for _, w in self.parts[0]["lines"]), default=0) if title else 0

    def _fit(self, text, path, start, minimum, max_w, max_lines, lh, color, gap, accent_ok=True):
        paras = parse_markup(text)
        size = max(start, minimum)
        while True:
            font = ImageFont.truetype(path, size)
            lines, space = wrap(paras, font, max_w)
            fits = len(lines) <= max_lines and all(w <= max_w for _, w in lines)
            if fits or size <= minimum:
                break
            size = int(size * 0.96)
        return dict(lines=lines, font=font, space=space, line_h=size * lh, color=color, gap=gap, size=size,
                    accent_ok=accent_ok)

    def draw(self, canvas: Image.Image, top: float):
        d = ImageDraw.Draw(canvas, "RGBA")
        y = top
        for p in self.parts:
            y += p["gap"]
            asc = p["font"].getmetrics()[0]
            for words, width in p["lines"]:
                x = (self.W - width) / 2 if self.align == "center" else self.margin
                for w, acc, ww in words:
                    fill = (*self.accent, 255) if (acc and p["accent_ok"]) else p["color"]
                    d.text((x, y + asc + (p["line_h"] - p["size"]) / 2 * 0.6), w, font=p["font"], fill=fill, anchor="ls")
                    x += ww + p["space"]
                y += p["line_h"]


# --------------------------------------------------------------------------- #
# Cihaz çerçevesi (ön yüz)
# --------------------------------------------------------------------------- #
def make_face(shot: Image.Image, dev: dict, colors, Bw: int, rim_frac: float = 0.008):
    light, dark = colors
    phone = dev["kind"] == "phone"
    sa = dev["size"][1] / dev["size"][0]
    bez = (0.026 if phone else 0.046) * Bw
    body_r = (0.158 if phone else 0.060) * Bw
    sw = int(round(Bw - 2 * bez))
    sh = int(round(sw * sa))
    Bh = int(round(sh + 2 * bez))
    pad = max(2, int(round(0.012 * Bw)))
    fw, fh = Bw + 2 * pad, Bh + 2 * pad
    mid = tuple(int(v) for v in lerp(light, dark, 0.5))
    face = Image.new("RGBA", (fw, fh), (*mid, 0))
    d = ImageDraw.Draw(face)

    if phone:
        bt = int(pad * 0.85)
        btn = tuple(int(v) for v in lerp(light, dark, 0.35)) + (255,)
        for (y0, y1) in ((0.135, 0.162), (0.185, 0.245), (0.262, 0.322)):
            d.rounded_rectangle((pad - bt, pad + Bh * y0, pad + 4, pad + Bh * y1), radius=bt // 2, fill=btn)
        d.rounded_rectangle((pad + Bw - 4, pad + Bh * 0.215, pad + Bw + bt, pad + Bh * 0.325), radius=bt // 2, fill=btn)

    ys, xs = np.mgrid[0:fh, 0:fw].astype(np.float32)
    t = (xs / fw * 0.55 + ys / fh * 0.45)
    stops = [0.0, 0.35, 0.7, 1.0]
    cols = [light, dark, lerp(light, dark, 0.25), dark]
    metal = np.stack([np.interp(t, stops, [c[i] for c in cols]) for i in range(3)], -1).astype(np.uint8)
    metal_im = Image.fromarray(metal, "RGB").convert("RGBA")
    outer = rr_mask((fw, fh), (pad, pad, pad + Bw, pad + Bh), body_r)
    face.paste(metal_im, (0, 0), outer)

    rim = min(rim_frac * Bw, bez * 0.92)
    inner = rr_mask((fw, fh), (pad + rim, pad + rim, pad + Bw - rim, pad + Bh - rim), body_r - rim)
    face.paste(Image.new("RGBA", (fw, fh), (8, 8, 10, 255)), (0, 0), inner)

    scr = ImageOps.fit(shot.convert("RGB"), (sw, sh), Image.LANCZOS).convert("RGBA")
    sr = body_r - bez
    if phone:
        iw, ih, iy = 0.285 * sw, 0.043 * sw * 1.9, 0.027 * sw
        pill = rr_mask((sw, sh), ((sw - iw) / 2, iy, (sw + iw) / 2, iy + ih), ih / 2)
        scr.paste(Image.new("RGBA", (sw, sh), (0, 0, 0, 255)), (0, 0), pill)
    gy, gx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    g = np.clip(1 - (gx / sw * 0.6 + gy / sh * 0.4) / 0.55, 0, 1) ** 2
    gloss = Image.fromarray((g * 30).astype(np.uint8), "L")
    scr = Image.alpha_composite(scr, Image.merge("RGBA", (*Image.new("RGB", (sw, sh), (255, 255, 255)).split(), gloss)))
    smask = rr_mask((sw, sh), (0, 0, sw, sh), sr)
    face.paste(scr, (int(round(pad + bez)), int(round(pad + bez))), smask)
    return face


# --------------------------------------------------------------------------- #
# 3B projeksiyon
# --------------------------------------------------------------------------- #
def rot_matrix(yaw, pitch, roll):
    y, p, r = map(math.radians, (yaw, pitch, roll))
    Ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    Rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return Rz @ Rx @ Ry


def project(pts, R, D):
    q = pts @ R.T
    s = D / (D - q[:, 2])
    return np.stack([q[:, 0] * s, q[:, 1] * s], -1)


def rounded_outline(w, h, r, n=14):
    pts = []
    for cx, cy, a0 in ((w / 2 - r, -h / 2 + r, -90), (w / 2 - r, h / 2 - r, 0),
                       (-w / 2 + r, h / 2 - r, 90), (-w / 2 + r, -h / 2 + r, 180)):
        for i in range(n + 1):
            a = math.radians(a0 + 90 * i / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return np.array(pts)


def homography(dst, src):
    A, b = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -x * u, -y * u])
        A.append([0, 0, 0, x, y, 1, -x * v, -y * v])
        b += [u, v]
    return np.linalg.solve(np.array(A, float), np.array(b, float))


def draw_phone(canvas, shot, dev, colors, p, region, persp=3.0, depth=0.085, glow=None, rim=0.008):
    """region = (x0, y0, x1, y1) SS piksel. glow = dict(color, blur, alpha) veya None."""
    CW, CH = canvas.size
    sa = dev["size"][1] / dev["size"][0]
    phone = dev["kind"] == "phone"
    bez_f = 0.026 if phone else 0.046
    bw0 = 1000.0
    sw0 = bw0 * (1 - 2 * bez_f)
    bh0 = sw0 * sa + bw0 * 2 * bez_f
    pad0 = 0.012 * bw0
    fw0, fh0 = bw0 + 2 * pad0, bh0 + 2 * pad0
    body_r0 = (0.158 if phone else 0.060) * bw0
    dz = depth * bw0
    R = rot_matrix(p["yaw"], p["pitch"], p["roll"])
    D = persp * bh0

    outline = rounded_outline(bw0, bh0, body_r0)
    front3 = np.c_[outline, np.full(len(outline), dz / 2)]
    back3 = np.c_[outline, np.full(len(outline), -dz / 2)]
    quad3 = np.array([[-fw0 / 2, -fh0 / 2, dz / 2], [fw0 / 2, -fh0 / 2, dz / 2],
                      [fw0 / 2, fh0 / 2, dz / 2], [-fw0 / 2, fh0 / 2, dz / 2]])
    allp = project(np.vstack([front3, back3, quad3]), R, D)
    x0, y0 = allp.min(0)
    x1, y1 = allp.max(0)
    bw_u, bh_u = x1 - x0, y1 - y0

    rx0, ry0, rx1, ry1 = region
    target_w = p["width"] * CW
    if p["mode"] == "fit":
        k = min(target_w / bw_u, (ry1 - ry0) / bh_u)
        cx = p.get("cx", 0.5) * CW
        cy = (ry0 + ry1) / 2 + p.get("dy", 0) * CH
        ox, oy = cx - k * (x0 + x1) / 2, cy - k * (y0 + y1) / 2
    else:
        k = target_w / bw_u
        cx = p.get("cx", 0.5) * CW
        ox, oy = cx - k * (x0 + x1) / 2, ry0 - k * y0

    def to_canvas(pts2):
        return pts2 * k + np.array([ox, oy])

    steps = 28
    light, dark = colors
    side_front = lerp(light, dark, 0.15)
    side_back = tuple(v * 0.35 for v in dark)
    polys = []
    for i in range(steps, -1, -1):
        z = dz / 2 - dz * i / steps
        pts = np.c_[outline, np.full(len(outline), z)]
        polys.append((i, [tuple(pt) for pt in to_canvas(project(pts, R, D))]))

    def shadow(dy, blur, alpha, color=(0, 0, 0), grow=0):
        q = 4
        m = Image.new("L", (CW // q, CH // q), 0)
        md = ImageDraw.Draw(m)
        for idx in (0, len(polys) - 1):
            md.polygon([(x / q, (y + dy) / q) for x, y in polys[idx][1]], fill=255)
        for _, poly in polys[1:-1:3]:
            md.polygon([(x / q, (y + dy) / q) for x, y in poly], fill=255)
        if grow:
            m = m.filter(ImageFilter.MaxFilter(grow * 2 + 1))
        m = m.filter(ImageFilter.GaussianBlur(max(0.5, blur / q))).resize((CW, CH), Image.BILINEAR)
        layer = Image.new("RGBA", (CW, CH), (*color, 0))
        layer.putalpha(m.point(lambda v: min(255, int(v * alpha))))
        return layer

    if glow:
        gc = hex2rgb(glow["color"]) if isinstance(glow["color"], str) else tuple(glow["color"])
        gb = float(glow.get("blur", 0.04)) * CW
        ga = float(glow.get("alpha", 0.6))
        canvas.alpha_composite(shadow(0, gb * 1.6, min(1.6, ga * 1.3), gc, grow=int(gb / 4 * 0.5)))
        canvas.alpha_composite(shadow(0, gb * 0.5, min(1.6, ga), gc, grow=2))
    canvas.alpha_composite(shadow(CH * 0.020, CW * 0.040, 0.45))
    canvas.alpha_composite(shadow(CH * 0.006, CW * 0.010, 0.35))

    d = ImageDraw.Draw(canvas)
    for i, poly in polys:
        t = i / steps
        col = tuple(int(v) for v in lerp(side_front, side_back, t ** 0.8))
        d.polygon(poly, fill=col)
        if i == 0:
            d.line(poly + [poly[0]], fill=tuple(int(v) for v in lerp(light, (255, 255, 255), 0.25)), width=max(1, SS))

    Bw_px = max(200, int(round(bw0 * k)))
    face = make_face(shot, dev, colors, Bw_px, rim)
    fw, fh = face.size
    dst = to_canvas(project(quad3, R, D))
    bx0, by0 = max(0, int(math.floor(dst[:, 0].min()))), max(0, int(math.floor(dst[:, 1].min())))
    bx1, by1 = min(CW, int(math.ceil(dst[:, 0].max()))), min(CH, int(math.ceil(dst[:, 1].max())))
    if bx1 <= bx0 or by1 <= by0:
        return
    coeffs = homography(dst - np.array([bx0, by0]), [(0, 0), (fw, 0), (fw, fh), (0, fh)])
    layer = face.transform((bx1 - bx0, by1 - by0), Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)
    canvas.alpha_composite(layer, (bx0, by0))


# --------------------------------------------------------------------------- #
# Dekorlar (şablon: blobs, pulse-line, squiggle, sparkle)
# --------------------------------------------------------------------------- #
def _color(c, default=(255, 255, 255)):
    if c is None:
        return default
    return hex2rgb(c) if isinstance(c, str) else tuple(c)


def deco_blobs(canvas, item, idx):
    CW, CH = canvas.size
    rnd = random.Random(idx * 31 + 5)
    q = 2
    layer = Image.new("RGBA", (CW // q, CH // q), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    col = _color(item.get("color"), (0, 0, 0))
    alpha = int(255 * float(item.get("alpha", 0.14)))
    n = int(item.get("count", 3))
    for j in range(n):
        cx = rnd.uniform(-0.05, 1.05) * CW / q
        cy = rnd.uniform(0.25, 1.0) * CH / q
        r = rnd.uniform(0.22, 0.42) * CW / q
        ph1, ph2 = rnd.uniform(0, 6.28), rnd.uniform(0, 6.28)
        pts = []
        for s in range(96):
            th = 2 * math.pi * s / 96
            rr = r * (1 + 0.22 * math.sin(3 * th + ph1) + 0.12 * math.sin(5 * th + ph2))
            pts.append((cx + rr * math.cos(th), cy + rr * math.sin(th) * 1.15))
        d.polygon(pts, fill=(*col, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(1.2)).resize((CW, CH), Image.BILINEAR)
    canvas.alpha_composite(layer)


def deco_pulse_line(canvas, item, idx, ctx):
    """ECG çizgisi: global x koordinatıyla çizilir, slaytlar yan yana konunca kesintisiz devam eder."""
    CW, CH = canvas.size
    y = ctx["line_y"] if item.get("at", "text-bottom") == "text-bottom" else float(item["at"]) * CH
    amp = float(item.get("amp", 0.038)) * CH
    period = float(item.get("period", 0.85)) * CW
    u = [0, .30, .345, .385, .43, .475, .52, .60, .66, 1.0]
    v = [0, 0, -.12, .20, -1.0, .45, 0, -.14, 0, 0]
    pts = []
    for x in range(0, CW + 1, 3):
        xg = idx * CW + x
        pts.append((x, y - amp * np.interp((xg % period) / period, u, v)))
    col = _color(item.get("color"), (190, 242, 100))
    th = max(2, int(float(item.get("thickness", 0.0034)) * CW))
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).line(pts, fill=(*col, 200), width=th * 3, joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(th * 2.2))
    canvas.alpha_composite(glow)
    ImageDraw.Draw(canvas).line(pts, fill=(*col, 255), width=th, joint="curve")


def deco_squiggle(canvas, item, ctx):
    W = canvas.size[0]
    col = _color(item.get("color"), (124, 58, 237))
    x0 = ctx["margin"]
    y = ctx["title_bottom"] + W * 0.016
    amp, wl, length = W * 0.0075, W * 0.052, W * float(item.get("length", 0.30))
    pts = [(x0 + s, y + amp * math.sin(2 * math.pi * s / wl)) for s in np.arange(0, length, 3)]
    ImageDraw.Draw(canvas).line(pts, fill=(*col, 255), width=max(3, int(W * 0.0065)), joint="curve")


def _star(d, cx, cy, r, col):
    pts = []
    for i in range(8):
        a = math.pi / 4 * i - math.pi / 2
        rr = r if i % 2 == 0 else r * 0.28
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d.polygon(pts, fill=col)


def deco_sparkle(canvas, item, ctx):
    W = canvas.size[0]
    col = (*_color(item.get("color"), (255, 255, 255)), 255)
    d = ImageDraw.Draw(canvas)
    cx = ctx["title_right"] + W * 0.075
    if cx > W * 0.93:  # başlık geniş: süs metne binmesin
        return
    cy = ctx["text_y"] + W * 0.050
    _star(d, cx, cy, W * 0.036, col)
    _star(d, cx - W * 0.055, cy + W * 0.070, W * 0.018, col)


# --------------------------------------------------------------------------- #
# Slayt üretimi
# --------------------------------------------------------------------------- #
def pick_text(v, locale):
    if v is None:
        return ""
    if isinstance(v, dict):
        return v.get(locale) or next(iter(v.values()), "")
    return str(v)


def text_problems(cfg: dict, locale: str | None) -> list[str]:
    """Çizimden önce: fontta olmayan karakterler ve en küçük boyutta bile sığmayan metin."""
    W, H = DEVICES[cfg["device"]]["size"]
    cw, ch = W * SS, H * SS
    title, subtitle = pick_text(cfg.get("title"), locale), pick_text(cfg.get("subtitle"), locale)
    fp_t = resolve_font(cfg.get("title_weight", "ExtraBold"), cfg.get("font"))
    fp_s = resolve_font("Regular", cfg.get("font"))
    problems = []
    missing = set()
    for text, path in ((title, fp_t), (subtitle, fp_s)):
        font = ImageFont.truetype(path, 64)
        notdef = bytes(font.getmask("\U000F0000"))  # özel kullanım alanı: fontun "eksik karakter" şekli
        missing |= {c for c in text if not c.isspace() and c != "*" and bytes(font.getmask(c)) == notdef}
    if missing:
        problems.append(f"fontta olmayan karakterler ({''.join(sorted(missing)[:8])}); --font ile uygun bir font verin.")
    tb = TextBlock(title, subtitle, cw, ch, fp_t, fp_s, (0, 0, 0, 255), (0, 0, 0), cfg.get("align", "center"),
                   float(cfg.get("title_scale", 1.0)))
    if any(w > cw - 2 * tb.margin for p in tb.parts for _, w in p["lines"]):
        problems.append("metin en küçük boyutta bile taşıyor; kısaltın veya boşlukla bölün.")
    return problems


def render_slide(cfg: dict, locale: str | None, idx: int = 0, total: int = 1) -> Image.Image:
    dev = DEVICES[cfg["device"]]
    W, H = dev["size"]
    cw, ch = W * SS, H * SS
    bg, lum = make_background((cw, ch), cfg["bg"], cfg.get("bg_angle"), glow=not cfg.get("no_glow"))
    dark_bg = lum < 0.58
    text_color = hex2rgb(cfg["text_color"]) if cfg.get("text_color") else ((255, 255, 255) if dark_bg else (17, 24, 39))
    accent = hex2rgb(cfg["accent"]) if cfg.get("accent") else ((253, 224, 71) if dark_bg else (124, 58, 237))
    canvas = bg
    deco = cfg.get("deco") or []

    title, subtitle = pick_text(cfg.get("title"), locale), pick_text(cfg.get("subtitle"), locale)
    fp_t = resolve_font(cfg.get("title_weight", "ExtraBold"), cfg.get("font"))
    fp_s = resolve_font("Regular", cfg.get("font"))
    tb = TextBlock(title, subtitle, cw, ch, fp_t, fp_s, (*text_color, 255), accent,
                   cfg.get("align", "center"), float(cfg.get("title_scale", 1.0)))
    top_margin, gap = ch * 0.060, ch * 0.030
    pos = cfg.get("text_position", "top")
    style = cfg["style"]
    if pos == "bottom":  # peek telefonu alta taşırır; aynı boyutta (2D/3D) kalan düzene geç
        style = {"peek": "tilt-right", "peek-flat": "flat"}.get(style, style)
    if pos == "top":
        text_y = top_margin
        region = (0, text_y + tb.height + gap, cw, ch - ch * 0.035)
    else:
        text_y = ch - top_margin - tb.height
        region = (0, ch * 0.035, cw, text_y - gap)
    if not title and not subtitle:
        region = (0, ch * 0.04, cw, ch * 0.96)

    title_right = (tb.margin + tb.title_w) if tb.align == "left" else (cw + tb.title_w) / 2
    ctx = dict(text_y=text_y, margin=tb.margin, title_bottom=text_y + tb.title_h, line_y=region[1] - gap * 0.45,
               title_right=title_right)
    for it in deco:  # telefonun arkasında kalanlar
        if it["type"] == "blobs":
            deco_blobs(canvas, it, idx)
        elif it["type"] == "pulse-line":
            deco_pulse_line(canvas, it, idx, ctx)

    shot = Image.open(cfg["image"]).convert("RGB")
    shot2 = Image.open(cfg["image2"]).convert("RGB") if cfg.get("image2") else shot
    colors = resolve_frame(cfg["frame"])
    phones = STYLES[style]["phones"]
    for p in phones:
        p = dict(p)
        if cfg.get("width") and len(phones) == 1:
            p["width"] = float(cfg["width"])
        draw_phone(canvas, shot2 if p.get("second") else shot, dev, colors, p, region,
                   persp=float(cfg.get("perspective", 3.0)), glow=cfg.get("glow"),
                   rim=float(cfg.get("frame_rim", 0.008)))
    tb.draw(canvas, text_y)
    for it in deco:  # metnin üstüne gelenler
        if it["type"] == "squiggle" and title:
            deco_squiggle(canvas, it, ctx)
        elif it["type"] == "sparkle":
            deco_sparkle(canvas, it, ctx)

    out = canvas.convert("RGB").resize((W, H), Image.LANCZOS)
    return out if cfg.get("no_grain") else add_grain(out)


def render_job(job: dict) -> str:
    """ProcessPool için üst düzey işlev. job: cfg, locale, idx, total, path, fmt"""
    img = render_slide(job["cfg"], job["locale"], job["idx"], job["total"])
    path = Path(job["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if job.get("fmt", "png") in ("jpg", "jpeg"):
        img.save(path, quality=95, subsampling=0)
    else:
        img.save(path, optimize=True)
    return str(path)
