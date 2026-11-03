"""
shotforge.palette — renk matematiği, şablon paleti, ekran görüntüsü analizi ve 2D/3D şablon seçim kuralları.

Kural özeti (shotforge rules):
  2D  = telefon düz ya da yalnızca düzlem içinde döndürülmüş (perspektif yok)
  3D  = telefon perspektifle eğik, kalınlığı görünür
"""
from __future__ import annotations

import colorsys
import re

import numpy as np
from PIL import Image

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
TOKEN_RE = re.compile(r"\$([a-z_]+)")


# --------------------------------------------------------------------------- #
# Renk yardımcıları
# --------------------------------------------------------------------------- #
def is_hex(s) -> bool:
    return isinstance(s, str) and bool(HEX_RE.match(s.strip()))


def hex2rgb(s: str) -> tuple[float, float, float]:
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return tuple(float(int(s[i:i + 2], 16)) for i in (0, 2, 4))  # type: ignore[return-value]


def rgb2hex(c) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in c)


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def lighten(c, t):
    return mix(c, (255, 255, 255), t)


def darken(c, t):
    return mix(c, (0, 0, 0), t)


def to_hls(c):
    return colorsys.rgb_to_hls(*(v / 255 for v in c))


def from_hls(h, l, s):
    return tuple(v * 255 for v in colorsys.hls_to_rgb(h % 1.0, max(0, min(1, l)), max(0, min(1, s))))


def rel_lum(c) -> float:
    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (f(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = rel_lum(a), rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


WHITE, INK = (255.0, 255.0, 255.0), (17.0, 24.0, 39.0)


def best_text(bg) -> tuple:
    return WHITE if contrast(WHITE, bg) >= contrast(INK, bg) else INK


def avg(cols):
    return tuple(sum(c[i] for c in cols) / len(cols) for i in range(3))


def ensure_text_contrast(fg, bgs, minimum=3.0):
    """Başlıklar büyük/kalın olduğundan WCAG 'büyük metin' eşiği (3:1) kullanılır."""
    worst = min(contrast(fg, b) for b in bgs)
    if worst >= minimum:
        return fg, False
    return best_text(avg(bgs)), True


def ensure_accent_contrast(ac, bgs, minimum=2.6):
    mid = avg(bgs)
    dark_bg = rel_lum(mid) < 0.3
    for _ in range(12):
        if min(contrast(ac, b) for b in bgs) >= minimum:
            return ac
        ac = lighten(ac, 0.12) if dark_bg else darken(ac, 0.12)
    return ac


def auto_accent(bg):
    h, l, s = to_hls(bg)
    if l < 0.55:  # koyu/orta zemin -> aynı tonun parlak, canlı hali (hafif kaydırılmış)
        return from_hls(h + 0.07, 0.66, max(0.7, s))
    return from_hls(h, 0.40, max(0.75, s))


# --------------------------------------------------------------------------- #
# Şablon paleti
# --------------------------------------------------------------------------- #
def build_palette(tpl: dict, colors: dict | None = None) -> dict:
    colors = colors or {}
    d = dict(tpl.get("palette") or {})
    for k in ("background", "text", "accent"):
        v = colors.get(k)
        if isinstance(v, (list, tuple)):
            v = v[0] if v else None
        if v:
            if not is_hex(v):
                raise ValueError(f"Geçersiz renk ({k}): {v}  (örnek: #6D28D9)")
            d[k] = v
    user_bg = bool(colors.get("background"))
    user_accent = bool(colors.get("accent"))
    bg = hex2rgb(d.get("background", "#6D28D9"))
    if d.get("accent") and (user_accent or not user_bg):
        accent = hex2rgb(d["accent"])
    else:
        accent = auto_accent(bg)
    h, l, s = to_hls(bg)
    return dict(
        bg=bg, accent=accent, user_text=hex2rgb(colors["text"]) if colors.get("text") else None,
        user_bg=user_bg, user_accent=user_accent,
        bg_dark=darken(bg, 0.30), bg_deep=darken(bg, 0.62), bg_lift=lighten(bg, 0.14),
        bg_tint=lighten(bg, 0.84), bg_soft=lighten(bg, 0.62),
        bg_warm=from_hls(h + 0.09, min(0.62, l + 0.12), max(s, 0.6)),
        accent_soft=lighten(accent, 0.55), accent_dark=darken(accent, 0.40),
    )


def resolve_placeholders(obj, tokens: dict[str, str]):
    if isinstance(obj, dict):
        return {k: resolve_placeholders(v, tokens) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_placeholders(v, tokens) for v in obj]
    if isinstance(obj, str):
        return TOKEN_RE.sub(lambda m: tokens.get(m.group(1), m.group(0)), obj)
    return obj


def slide_style(tpl: dict, pal: dict, idx: int, warnings: list) -> dict:
    """Şablonun bg_mode'una göre slayt başına arka plan, yazı ve vurgu rengi. Placeholder token'larını da döndürür."""
    bg, ac = pal["bg"], pal["accent"]
    mode = tpl.get("bg_mode")
    angle, no_glow = 160, True

    if mode is None:  # eski tarz şablon: bg_cycle
        cyc = tpl.get("bg_cycle") or [{"bg": "violet"}]
        e = dict(cyc[idx % len(cyc)])
        if pal["user_bg"]:
            e["bg"] = f"{rgb2hex(darken(bg, .3))},{rgb2hex(bg)}"
        if pal["user_text"]:
            e["text_color"] = rgb2hex(pal["user_text"])
        if pal["user_accent"]:
            e["accent"] = rgb2hex(ac)
        tokens = _tokens(pal, ac, hex2rgb(e["text_color"]) if e.get("text_color") else WHITE)
        return dict(e, tokens=tokens)

    if mode == "dark-gradient":
        stops, angle = [darken(bg, 0.35), bg], 165
        text, accent = best_text(bg), ac
    elif mode == "gradient":
        stops, no_glow = [darken(bg, 0.45), bg, pal["bg_warm"]], False
        text, accent = best_text(bg), ac
    elif mode == "light-gradient":
        stops = [lighten(bg, 0.90), lighten(bg, 0.66)]
        text, accent = darken(bg, 0.72), (ac if pal["user_accent"] else bg)
    elif mode == "alternate-tint":
        if idx % 3 == 0:
            stops = [lighten(bg, 0.86), lighten(bg, 0.74)]
            text, accent = darken(bg, 0.60), (ac if pal["user_accent"] else bg)
        else:
            stops, angle = [bg, darken(bg, 0.10)], 165
            text, accent = best_text(bg), (ac if pal["user_accent"] else lighten(bg, 0.75))
    else:
        raise SystemExit(f"Bilinmeyen bg_mode: {mode}")

    if pal["user_text"] is not None:
        text, changed = ensure_text_contrast(pal["user_text"], stops)
        if changed:
            warnings.append((idx + 1, f"yazı rengi {rgb2hex(pal['user_text'])} bu arka planda okunaksız "
                                      f"(kontrast < 3:1); {rgb2hex(text)} kullanıldı."))
    accent = ensure_accent_contrast(accent, stops)
    spec = ",".join(rgb2hex(c) for c in stops)
    return dict(bg=spec, bg_angle=angle, text_color=rgb2hex(text), accent=rgb2hex(accent), no_glow=no_glow,
                tokens=_tokens(pal, accent, text))


def _tokens(pal, accent, text) -> dict[str, str]:
    t = {k: rgb2hex(pal[k]) for k in ("bg", "bg_dark", "bg_deep", "bg_lift", "bg_tint", "bg_soft", "bg_warm",
                                       "accent_soft", "accent_dark")}
    t["accent"], t["text"] = rgb2hex(accent), rgb2hex(text)
    return t


# --------------------------------------------------------------------------- #
# Ekran görüntüsü analizi
# --------------------------------------------------------------------------- #
def _clusters(img) -> list[tuple]:
    q = img.quantize(colors=10, method=Image.MEDIANCUT)
    pal_flat = q.getpalette()[:30]
    total = sum(c for c, _ in q.getcolors())
    out = []
    for count, idx in q.getcolors():
        rgb = tuple(float(v) for v in pal_flat[idx * 3: idx * 3 + 3])
        h, l, s = to_hls(rgb)
        out.append((count / total, rgb, l, s))
    return sorted(out, key=lambda c: -c[0])


def _brand_candidates(clusters) -> list[tuple]:
    return [c for c in clusters if c[3] > 0.35 and 0.2 < c[2] < 0.8 and c[0] > 0.012]


def analyze_shots(paths) -> dict:
    thumbs, grads, lums = [], [], []
    for p in paths:
        im = Image.open(p).convert("RGB")
        w = 240
        t = im.resize((w, max(1, round(im.height * w / im.width))), Image.BILINEAR)
        a = np.asarray(t, dtype=np.float32)
        gray = a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        grads.append((np.abs(np.diff(gray, axis=1)).mean() + np.abs(np.diff(gray, axis=0)).mean()) / 2)
        lums.append(float(gray.mean() / 255))
        thumbs.append(t.resize((64, 64)))
    big = Image.new("RGB", (64 * len(thumbs), 64))
    for i, t in enumerate(thumbs):
        big.paste(t, (i * 64, 0))
    clusters = _clusters(big)
    cand = _brand_candidates(clusters)
    if not cand:  # farklı renkli ekranlar birlikte nicelenince griye ortalanır; tek tek bak
        cand = [c for t in thumbs for c in _brand_candidates(_clusters(t))]
    brand = rgb2hex(max(cand, key=lambda c: c[0] * c[3])[1]) if cand else None
    gs = float(np.mean(grads))
    return dict(
        count=len(paths), brand=brand, palette=[rgb2hex(c[1]) for c in clusters[:5]],
        ui="light" if np.mean(lums) > 0.55 else "dark",
        density="low" if gs < 2.2 else ("medium" if gs < 4.5 else "high"), density_score=round(gs, 2),
    )


# --------------------------------------------------------------------------- #
# Kategori ve 2D/3D şablon seçim kuralları
# --------------------------------------------------------------------------- #
CATEGORY_KEYWORDS = {
    "fitness": ["fitness", "workout", "gym", "running", "egzersiz", "spor", "antrenman", "koşu"],
    "health": ["health", "wellness", "meditation", "sleep", "habit", "sağlık", "uyku", "meditasyon", "alışkanlık"],
    "finance": ["finance", "bank", "budget", "invest", "money", "finans", "banka", "bütçe", "para", "yatırım"],
    "social": ["social", "chat", "message", "community", "friends", "sosyal", "sohbet", "topluluk", "arkadaş"],
    "education": ["learn", "language", "course", "study", "education", "öğren", "dil ", "kurs", "eğitim", "ders"],
    "productivity": ["todo", "task", "planner", "notes", "calendar", "productivity", "görev", "plan", "not ", "takvim", "verimlilik"],
    "games": ["game", "puzzle", "arcade", "oyun", "bulmaca"],
    "photo": ["photo", "camera", "video", "edit", "fotoğraf", "kamera", "düzenle"],
    "travel": ["travel", "trip", "flight", "hotel", "map", "seyahat", "gezi", "uçuş", "otel", "harita"],
    "shopping": ["shop", "store", "deal", "alışveriş", "mağaza", "indirim"],
    "entertainment": ["music", "movie", "stream", "podcast", "müzik", "film", "eğlence"],
    "lifestyle": ["recipe", "food", "fashion", "dating", "pet", "tarif", "yemek", "moda"],
    "utilities": ["tool", "scanner", "vpn", "utility", "converter", "araç", "tarayıcı"],
}
CATEGORIES = list(CATEGORY_KEYWORDS)
CAT_3D = {"games", "photo", "travel", "shopping", "entertainment", "lifestyle"}
CAT_2D_STRONG = {"finance", "productivity", "utilities", "health"}
CAT_2D_WEAK = {"fitness", "education"}

RULES_TEXT = """\
2D / 3D ve şablon seçim kuralları (--template auto iken)
  2D = telefon düz veya yalnızca düzlem içinde döndürülmüş; 3D = perspektifli, kalınlığı görünen eğik telefon.
  Boyut puanı (>=1 ise 3D, değilse 2D):
    +2  kategori: games, photo, travel, shopping, entertainment, lifestyle
    -2  kategori: finance, productivity, utilities, health      (-1: fitness, education)
    +1  en çok 3 farklı ekran görüntüsü (vitrin / hero etkisi)
    -2  ekran görüntüleri yoğun, küçük yazılı (okunabilirlik için düz kalmalı)
    +1  ton: premium, playful, creative, bold
  Boyut --dimension 2d|3d ile zorlanabilir; --template verilirse hiçbir otomatik seçim yapılmaz.
  Seçilen boyutun içinde şablon puanı: +3 tema uyumu (koyu ana renk -> dark, çok açık -> light, canlı orta ton -> colorful),
  +3 kategori uyumu (şablonun best_for listesi); eşitlikte şablon sırası.
  Kullanıcı arka plan rengi verdiyse, temayı o renkle uyumsuz bir şablon (ör. koyu renk + açık şablon) seçilmez; Claude önerse bile kural motoru değiştirir.
"""


def infer_category(text: str) -> str | None:
    t = " " + (text or "").lower() + " "
    best, score = None, 0
    for cat, kws in CATEGORY_KEYWORDS.items():
        sc = sum(t.count(k) for k in kws)
        if sc > score:
            best, score = cat, sc
    return best


def default_colors(profile: dict | None, chosen_theme: str | None = None) -> dict:
    """Kullanıcı ve Claude renk vermediyse ekran görüntülerinden türetilen renkler.
    chosen_theme: kullanıcının açıkça seçtiği şablonun teması. Koyu bir şablonu seçtiyse koyu arka planı kalır, marka rengi
    yalnızca vurgu olur. Renk nötrse şablon varsayılanı kalır."""
    brand = (profile or {}).get("brand")
    if not brand:
        return {}
    return {"accent": brand} if chosen_theme == "dark" else {"background": brand}


def desired_theme(bg_hex: str | None, profile: dict | None) -> str:
    if bg_hex and is_hex(bg_hex):
        L = rel_lum(hex2rgb(bg_hex))
        if L < 0.12:
            return "dark"
        if L > 0.60:
            return "light"
        return "colorful"
    if profile and profile.get("brand"):
        h, l, s = to_hls(hex2rgb(profile["brand"]))
        if s > 0.45 and 0.25 < l < 0.75:
            return "colorful"
    return "dark"


def choose_template(templates: dict, *, dimension: str | None = None, category: str | None = None,
                    bg_hex: str | None = None, profile: dict | None = None, tone: str = "") -> tuple[str, str, list[str]]:
    reasons: list[str] = []
    if dimension in ("2d", "3d"):
        dim = dimension
        reasons.append(f"boyut {dim.upper()} olarak istendi")
    else:
        score = 0
        if category in CAT_3D:
            score += 2
            reasons.append(f"kategori '{category}' 3D'ye yatkın (+2)")
        elif category in CAT_2D_STRONG:
            score -= 2
            reasons.append(f"kategori '{category}' 2D'ye yatkın (-2)")
        elif category in CAT_2D_WEAK:
            score -= 1
            reasons.append(f"kategori '{category}' 2D'ye hafif yatkın (-1)")
        if profile:
            if profile.get("count", 99) <= 3:
                score += 1
                reasons.append("en çok 3 farklı ekran: vitrin etkisi (+1)")
            if profile.get("density") == "high":
                score -= 2
                reasons.append("ekranlar yoğun/küçük yazılı: okunabilirlik için düz (-2)")
        if re.search(r"premium|playful|creative|bold|eğlenceli|yaratıcı", tone or "", re.I):
            score += 1
            reasons.append("ton premium/eğlenceli/yaratıcı (+1)")
        dim = "3d" if score >= 1 else "2d"
        reasons.append(f"toplam puan {score:+d} -> {dim.upper()}")
    pool = {n: t for n, t in templates.items() if t.get("dimension") == dim}
    if not pool:
        pool = dict(templates)
    theme = desired_theme(bg_hex, profile)
    best, best_s = None, -1
    for n, t in pool.items():
        s = 0
        if t.get("theme") == theme:
            s += 3
        if category and category in (t.get("best_for") or []):
            s += 3
        if s > best_s:
            best, best_s = n, s
    reasons.append(f"tema '{theme}', kategori '{category or '?'}' -> {best} (puan {best_s})")
    return best, templates[best].get("dimension", dim), reasons
