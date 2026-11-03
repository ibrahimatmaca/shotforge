#!/usr/bin/env python3
"""Deneme için sahte 'uygulama' ekranları üretir (1320x2868). Kendi ekranlarınız yoksa bununla test edin."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FONTS = HERE.parent / "shotforge" / "fonts"
W, H = 1320, 2868
out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "input"
out.mkdir(parents=True, exist_ok=True)


def F(w, s):
    return ImageFont.truetype(str(FONTS / f"Inter-{w}.otf"), s)


def base(title, accent):
    im = Image.new("RGB", (W, H), (246, 247, 251))
    d = ImageDraw.Draw(im)
    d.text((70, 60), "9:41", font=F("Bold", 48), fill=(20, 20, 30))
    for i in range(H // 4):  # üst degrade başlık
        t = i / (H // 4)
        c = tuple(int(accent[0][j] * (1 - t) + accent[1][j] * t) for j in range(3))
        d.line((0, 180 + i, W, 180 + i), fill=c)
    d.text((70, 260), title, font=F("ExtraBold", 88), fill="white")
    d.text((70, 380), "Pazartesi, 3 Kasım", font=F("Regular", 44), fill=(255, 255, 255))
    return im, d


def card(d, y, h, label, value, col):
    d.rounded_rectangle((60, y, W - 60, y + h), radius=56, fill="white", outline=(228, 230, 240), width=3)
    d.rounded_rectangle((100, y + 40, 100 + 110, y + 150), radius=34, fill=col)
    d.text((250, y + 36), label, font=F("SemiBold", 48), fill=(30, 32, 48))
    d.text((250, y + 104), value, font=F("Regular", 40), fill=(120, 124, 144))


def screen_home():
    im, d = base("Bugün", ((91, 33, 182), (219, 39, 119)))
    y = 900
    for lab, val, col in [("Sabah koşusu", "5,2 km  •  28 dk", (236, 72, 153)), ("Su içmek", "6 / 8 bardak", (59, 130, 246)),
                          ("Meditasyon", "10 dk  •  tamamlandı", (16, 185, 129)), ("Okuma", "32 sayfa", (245, 158, 11)),
                          ("Uyku", "7 sa 40 dk", (99, 102, 241)), ("Yürüyüş", "8.420 adım", (239, 68, 68))]:
        card(d, y, 250, lab, val, col)
        y += 290
    return im


def screen_stats():
    im, d = base("İstatistikler", ((30, 64, 175), (6, 182, 212)))
    d.rounded_rectangle((60, 900, W - 60, 1900), radius=64, fill="white")
    vals = [0.4, 0.62, 0.5, 0.85, 0.7, 0.95, 0.6]
    for i, v in enumerate(vals):
        x = 150 + i * 150
        d.rounded_rectangle((x, 1800 - int(760 * v), x + 90, 1800), radius=30, fill=(37, 99, 235) if i != 5 else (236, 72, 153))
        d.text((x + 18, 1830), "PSÇPCCP"[i], font=F("SemiBold", 40), fill=(120, 124, 144))
    d.text((120, 940), "Bu hafta", font=F("Bold", 56), fill=(30, 32, 48))
    card(d, 2000, 250, "Seri", "21 gün üst üste", (245, 158, 11))
    card(d, 2290, 250, "Rekor", "Pazar  •  12,4 km", (16, 185, 129))
    return im


def screen_goals():
    im, d = base("Hedefler", ((6, 95, 70), (52, 211, 153)))
    cx, cy, r = W // 2, 1350, 380
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(220, 224, 236), width=60)
    d.arc((cx - r, cy - r, cx + r, cy + r), start=-90, end=170, fill=(16, 185, 129), width=60)
    d.text((cx, cy - 20), "72%", font=F("ExtraBold", 190), fill=(30, 32, 48), anchor="mm")
    d.text((cx, cy + 130), "haftalık hedef", font=F("Regular", 52), fill=(120, 124, 144), anchor="mm")
    for i, (lab, val, col) in enumerate([("30 dk egzersiz", "5 / 7 gün", (16, 185, 129)), ("Şekersiz hafta", "4 / 7 gün", (245, 158, 11)),
                                          ("10.000 adım", "3 / 7 gün", (59, 130, 246))]):
        card(d, 1900 + i * 290, 250, lab, val, col)
    return im


def screen_profile():
    im, d = base("Profil", ((124, 45, 18), (251, 146, 60)))
    d.ellipse((W // 2 - 170, 820, W // 2 + 170, 1160), fill=(255, 255, 255), outline=(251, 191, 36), width=14)
    d.text((W // 2, 990), "AM", font=F("ExtraBold", 140), fill=(124, 45, 18), anchor="mm")
    d.text((W // 2, 1260), "Alex Morgan", font=F("Bold", 68), fill=(30, 32, 48), anchor="mm")
    d.text((W // 2, 1345), "Pro üyelik  •  Eylül 2025'ten beri", font=F("Regular", 44), fill=(120, 124, 144), anchor="mm")
    y = 1500
    for lab, val, col in [("Bildirimler", "Açık", (99, 102, 241)), ("Apple Sağlık", "Bağlı", (239, 68, 68)),
                          ("Tema", "Otomatik", (16, 185, 129)), ("Dil", "Türkçe", (245, 158, 11))]:
        card(d, y, 250, lab, val, col)
        y += 290
    return im


for name, fn in [("01_home", screen_home), ("02_stats", screen_stats), ("03_goals", screen_goals), ("04_profile", screen_profile)]:
    fn().save(out / f"{name}.png")
    print("yazıldı:", out / f"{name}.png")
